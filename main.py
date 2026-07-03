"""
Main bot loop — one trading cycle per invocation ("check-then-act").

Run this on a schedule (systemd timer on the Lightsail box in deploy/, or a
Lambda handler wrapping run_cycle() later if the Robinhood auth story ends up
supporting headless clients). The scheduling mechanism is intentionally
decoupled from this file — main() just does one cycle and exits.

Cycle steps:
  1. Kill switch / daily-loss risk check (before touching anything else)
  2. Pull live price data for the configured symbol from Robinhood
  3. Compute the active strategy's signal
  4. Decide BUY / SELL / HOLD, size the order against risk limits
  5. DRY_RUN: log what would happen via review_equity_order (no real order)
     Live:    place_equity_order, then notify
  6. Log the decision (durable), notify on trades/errors/halts

IMPORTANT — market vs. limit orders: Robinhood only allows fractional
shares on type="market" orders. SPY trades around $745/share; a $100
account capped at 50% per position ($50 max) can never buy a whole share,
so BUY orders here are dollar-based market orders, not limit orders. This
is a deliberate deviation from the original "always use limit orders"
preference — see robinhood_client.py's docstring for the full reasoning.
"""
import asyncio
import traceback
import uuid
from datetime import datetime, timedelta, timezone

import pandas as pd

import config
from decision_log import DecisionLogEntry, DecisionLogger
from risk import RiskManager
from robinhood_client import RobinhoodClient
from strategies import get_strategy


def notify(subject: str, message: str) -> None:
    """Send an SNS notification. No-ops with a console print if SNS isn't
    configured (e.g. running locally)."""
    print(f"[NOTIFY] {subject}: {message}")
    if not config.SNS_TOPIC_ARN:
        return
    try:
        import boto3

        sns = boto3.client("sns", region_name=config.AWS_REGION)
        sns.publish(TopicArn=config.SNS_TOPIC_ARN, Subject=subject[:100], Message=message)
    except Exception as e:
        print(f"[main] WARNING: failed to send SNS notification: {e!r}")


def build_strategy():
    return get_strategy(
        config.STRATEGY,
        fast_window=config.TF_FAST_WINDOW,
        slow_window=config.TF_SLOW_WINDOW,
        window=config.MR_WINDOW,
        entry_z=config.MR_ENTRY_Z,
        exit_z=config.MR_EXIT_Z,
    )


async def run_cycle() -> None:
    if config.DRY_RUN:
        print(f"[{datetime.now(timezone.utc).isoformat()}] Starting cycle — DRY_RUN=true (no real orders)")
    else:
        print(
            f"[{datetime.now(timezone.utc).isoformat()}] "
            f"Starting cycle — DRY_RUN=FALSE, LIVE ORDERS ENABLED"
        )

    risk = RiskManager()
    logger = DecisionLogger()
    rh = RobinhoodClient()
    strategy = build_strategy()
    symbol = config.SYMBOLS[0]

    # ── 1. Account snapshot + risk pre-check ────────────────────────────
    try:
        account_number = await rh.get_agentic_account_number()
        portfolio = await rh.get_portfolio(account_number)
        account_value = _extract_account_value(portfolio)
    except Exception as e:
        notify("Trading bot ERROR", f"Failed to fetch portfolio: {e!r}\n{traceback.format_exc()}")
        return

    pre_check = risk.pre_cycle_check(account_value)
    if not pre_check.allowed:
        logger.log(
            DecisionLogEntry(
                symbol=symbol,
                strategy=strategy.name,
                signal_value=0.0,
                action="SKIPPED",
                reason=pre_check.reason,
                quantity=0,
                price=0,
                resulting_position_shares=0,
                account_balance_after=account_value,
                dry_run=config.DRY_RUN,
            )
        )
        notify("Trading bot HALTED", pre_check.reason)
        return

    # ── 2. Live price history + signal ──────────────────────────────────
    try:
        prices = await _fetch_price_history(rh, symbol)
        signals = strategy.compute_signals(prices)
        latest = signals.iloc[-1]
    except Exception as e:
        notify("Trading bot ERROR", f"Failed to compute signal: {e!r}\n{traceback.format_exc()}")
        return

    positions = await rh.get_equity_positions(account_number)
    current_shares = _extract_shares(positions, symbol)
    in_position = current_shares > 0
    signal_col = "z_score" if "z_score" in signals.columns else "fast_ma"
    signal_value = float(latest[signal_col]) if pd.notna(latest[signal_col]) else 0.0

    action = "HOLD"
    quantity = 0.0
    order_dollars = 0.0
    price = float(latest["price"])
    reason = latest.get("description", "")

    if latest["signal"] == 1 and not in_position:
        order_dollars = round(account_value * config.MAX_POSITION_PCT, 2)
        check = risk.check_order(order_dollars, account_value)
        if not check.allowed:
            action = "SKIPPED"
            reason = check.reason
        else:
            action = "BUY"
            quantity = round(order_dollars / price, 6)  # estimate; market order fills at real-time price
    elif latest["signal"] == 0 and in_position:
        action = "SELL"
        quantity = current_shares

    # ── 3. Execute (dry-run: simulate only; live: real order) ──────────
    order_id = None
    if action in ("BUY", "SELL") and quantity > 0:
        side = "buy" if action == "BUY" else "sell"
        try:
            if config.DRY_RUN:
                if action == "BUY":
                    review = await rh.review_equity_order(
                        account_number, symbol, side, "market", dollar_amount=f"{order_dollars:.2f}"
                    )
                else:
                    review = await rh.review_equity_order(
                        account_number, symbol, side, "market", quantity=f"{quantity:.6f}"
                    )
                reason = f"DRY RUN — would {action} ~{quantity} {symbol} @ ~${price:.2f}. Review: {review}"
            else:
                ref_id = str(uuid.uuid4())
                if action == "BUY":
                    result = await rh.place_equity_order(
                        account_number, symbol, side, "market",
                        dollar_amount=f"{order_dollars:.2f}", ref_id=ref_id,
                    )
                else:
                    result = await rh.place_equity_order(
                        account_number, symbol, side, "market",
                        quantity=f"{quantity:.6f}", ref_id=ref_id,
                    )
                order_id = result.get("data", {}).get("id") or ref_id
                notify(
                    f"Trading bot: LIVE {action} placed",
                    f"~{quantity} {symbol} (~${order_dollars or quantity * price:.2f}) @ ~${price:.2f}. "
                    f"Order: {order_id}",
                )
        except Exception as e:
            notify("Trading bot ERROR", f"Order execution failed: {e!r}\n{traceback.format_exc()}")
            action = "SKIPPED"
            reason = f"order execution error: {e!r}"

    resulting_shares = current_shares + (quantity if action == "BUY" else -quantity if action == "SELL" else 0)

    logger.log(
        DecisionLogEntry(
            symbol=symbol,
            strategy=strategy.name,
            signal_value=signal_value,
            action=action,
            reason=str(reason),
            quantity=quantity,
            price=price,
            resulting_position_shares=resulting_shares,
            account_balance_after=account_value,
            dry_run=config.DRY_RUN,
            order_id=order_id,
        )
    )
    print(f"[main] cycle complete: {action} {quantity} {symbol} — {reason}")


async def _fetch_price_history(rh: RobinhoodClient, symbol: str) -> pd.Series:
    """Pulls enough daily history for the active strategy's rolling window."""
    lookback_days = max(config.TF_SLOW_WINDOW, config.MR_WINDOW) * 2 + 10  # buffer for weekends/holidays
    start_time = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).strftime("%Y-%m-%dT00:00:00Z")
    result = await rh.get_equity_historicals([symbol], start_time=start_time, interval="day")
    return _parse_historicals(result, symbol)


def _parse_historicals(result: dict, symbol: str) -> pd.Series:
    """
    Verified live against a real get_equity_historicals call. Shape:
    {"data": {"results": [{"symbol": "SPY", "bars": [
        {"begins_at": "2026-07-02T00:00:00Z", "close_price": "744.780000", ...}, ...
    ]}]}}
    """
    data = result.get("data", result)
    results = data.get("results", [])
    match = next((r for r in results if r.get("symbol") == symbol), None)
    if not match or not match.get("bars"):
        raise ValueError(f"No historical bars returned for {symbol}: {result!r}")
    bars = match["bars"]
    dates = pd.DatetimeIndex([pd.Timestamp(b["begins_at"]) for b in bars])
    closes = [float(b["close_price"]) for b in bars]
    return pd.Series(closes, index=dates, name="Close")


def _extract_account_value(portfolio: dict) -> float:
    """
    Verified live against a real get_portfolio call. Shape:
    {"data": {"total_value": "100", "cash": "100",
               "buying_power": {"buying_power": "100.0000", ...}, ...}}
    """
    data = portfolio.get("data", portfolio)
    return float(data["total_value"])


def _extract_shares(positions: dict, symbol: str) -> float:
    """
    Verified live against a real get_equity_positions call. Shape:
    {"data": {"positions": [
        {"symbol": "SPY", "quantity": "0.134", "shares_available_for_sells": "0.134", ...}
    ]}}
    Empty positions list is a valid, common response (verified: fresh
    accounts return {"data": {"positions": []}}).
    """
    data = positions.get("data", positions)
    for p in data.get("positions", []):
        if p.get("symbol") == symbol:
            return float(p.get("quantity", 0))
    return 0.0


def main():
    asyncio.run(run_cycle())


if __name__ == "__main__":
    main()
