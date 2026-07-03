"""
Main bot loop — one trading cycle per invocation ("check-then-act").

Run this on a schedule (systemd timer / cron on an always-on instance, or a
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
"""
import asyncio
import os
import sys
import traceback
from datetime import datetime, timezone

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
        portfolio = await rh.get_portfolio()
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

    positions = await rh.get_equity_positions()
    current_shares = _extract_shares(positions, symbol)
    in_position = current_shares > 0
    signal_col = "z_score" if "z_score" in signals.columns else "fast_ma"
    signal_value = float(latest[signal_col]) if pd.notna(latest[signal_col]) else 0.0

    action = "HOLD"
    quantity = 0.0
    price = float(latest["price"])
    reason = latest.get("description", "")

    if latest["signal"] == 1 and not in_position:
        order_dollars = account_value * config.MAX_POSITION_PCT
        check = risk.check_order(order_dollars, account_value)
        if not check.allowed:
            action = "SKIPPED"
            reason = check.reason
        else:
            action = "BUY"
            quantity = round(order_dollars / price, 6)
    elif latest["signal"] == 0 and in_position:
        action = "SELL"
        quantity = current_shares

    # ── 3. Execute (dry-run: simulate only; live: real order) ──────────
    order_id = None
    if action in ("BUY", "SELL") and quantity > 0:
        side = "buy" if action == "BUY" else "sell"
        try:
            if config.DRY_RUN:
                review = await rh.review_equity_order(symbol, side, quantity, order_type="limit", limit_price=price)
                reason = f"DRY RUN — would {action} {quantity} {symbol} @ ~${price:.2f}. Review: {review}"
            else:
                result = await rh.place_equity_order(symbol, side, quantity, order_type="limit", limit_price=price)
                order_id = getattr(result, "order_id", None) or str(result)
                notify(
                    f"Trading bot: LIVE {action} placed",
                    f"{quantity} {symbol} @ ~${price:.2f}. Order: {order_id}",
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
    lookback = max(config.TF_SLOW_WINDOW, config.MR_WINDOW) + 5
    result = await rh.get_equity_historicals(symbol, lookback_days=lookback)
    return _parse_historicals(result)


def _parse_historicals(result) -> pd.Series:
    """
    UNVERIFIED — placeholder parser. Robinhood's real get_equity_historicals
    response shape hasn't been seen yet (needs a live connection). Once you
    have one, call it once, print the raw result, and rewrite this function
    to match. Expected shape based on the tool description ("OHLCV price
    bars across a time range") is a list of bars with a close price and a
    timestamp — adjust the field names below once confirmed.
    """
    raise NotImplementedError(
        "Update _parse_historicals() to match the real get_equity_historicals "
        "response once you have a live Robinhood MCP connection to inspect it."
    )


def _extract_account_value(portfolio) -> float:
    """Robinhood's get_portfolio response shape needs to be confirmed against
    a real connection — adjust this parser once you can see real output."""
    raise NotImplementedError("Parse the real get_portfolio() response shape once connected.")


def _extract_shares(positions, symbol: str) -> float:
    raise NotImplementedError("Parse the real get_equity_positions() response shape once connected.")


def main():
    asyncio.run(run_cycle())


if __name__ == "__main__":
    main()
