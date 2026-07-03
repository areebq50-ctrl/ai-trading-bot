"""
Backtesting engine — uses yfinance only, no Robinhood connection needed.

Usage:
    python backtest.py                           # uses config.py defaults
    python backtest.py --symbol QQQ --start 2021-01-01 --end 2023-12-31 --strategy trend_following
"""
import argparse
import sys
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd
import yfinance as yf

import config
from strategies import get_strategy


@dataclass
class BacktestResult:
    symbol: str
    strategy_name: str
    start: str
    end: str
    total_return_pct: float
    buy_hold_return_pct: float
    max_drawdown_pct: float
    num_trades: int
    commission_cost_pct: float
    net_return_pct: float
    equity_curve: pd.Series = field(repr=False)
    trades: list = field(repr=False, default_factory=list)


def fetch_prices(symbol: str, start: str, end: str) -> pd.Series:
    ticker = yf.Ticker(symbol)
    df = ticker.history(start=start, end=end, auto_adjust=True)
    if df.empty:
        raise ValueError(f"No price data returned for {symbol} ({start} → {end})")
    return df["Close"].dropna()


def max_drawdown(equity: pd.Series) -> float:
    """Maximum peak-to-trough drawdown as a positive percentage."""
    rolling_max = equity.cummax()
    drawdown = (equity - rolling_max) / rolling_max
    return abs(drawdown.min()) * 100


def run_backtest(
    symbol: str,
    start: str,
    end: str,
    strategy_name: str,
    fast_window: int = config.TF_FAST_WINDOW,
    slow_window: int = config.TF_SLOW_WINDOW,
    mr_window: int = config.MR_WINDOW,
    mr_entry_z: float = config.MR_ENTRY_Z,
    mr_exit_z: float = config.MR_EXIT_Z,
    commission_bps: float = config.COMMISSION_BPS,
    initial_capital: float = config.MAX_CAPITAL,
) -> BacktestResult:
    prices = fetch_prices(symbol, start, end)

    strategy = get_strategy(
        strategy_name,
        fast_window=fast_window,
        slow_window=slow_window,
        window=mr_window,
        entry_z=mr_entry_z,
        exit_z=mr_exit_z,
    )
    signals_df = strategy.compute_signals(prices)

    # ── Simulate portfolio ─────────────────────────────────────────────────────
    capital = initial_capital
    shares = 0.0
    trades = []
    equity = []
    prev_signal = 0

    commission_rate = commission_bps / 10_000

    for date, row in signals_df.iterrows():
        sig = row["signal"]
        price = row["price"]

        # Transition: flat → long (buy)
        if sig == 1 and prev_signal == 0:
            shares_to_buy = (capital * config.MAX_POSITION_PCT) / price
            cost = shares_to_buy * price
            commission = cost * commission_rate
            capital -= cost + commission
            shares += shares_to_buy
            trades.append(
                {
                    "date": date,
                    "side": "BUY",
                    "shares": shares_to_buy,
                    "price": price,
                    "commission": commission,
                    "capital_before": capital + cost + commission,  # capital before buy
                    "description": row.get("description", ""),
                }
            )

        # Transition: long → flat (sell)
        elif sig == 0 and prev_signal == 1 and shares > 0:
            proceeds = shares * price
            commission = proceeds * commission_rate
            capital += proceeds - commission
            trades.append(
                {
                    "date": date,
                    "side": "SELL",
                    "shares": shares,
                    "price": price,
                    "commission": commission,
                    "description": row.get("description", ""),
                }
            )
            shares = 0.0

        equity.append(capital + shares * price)
        prev_signal = sig

    # Close any open position at end
    if shares > 0:
        final_price = signals_df["price"].iloc[-1]
        proceeds = shares * final_price
        commission = proceeds * commission_rate
        capital += proceeds - commission
        equity[-1] = capital
        shares = 0.0

    equity_series = pd.Series(equity, index=signals_df.index)
    total_return = (equity_series.iloc[-1] / initial_capital - 1) * 100
    buy_hold_return = (prices.iloc[-1] / prices.iloc[0] - 1) * 100
    total_commission = sum(t["commission"] for t in trades)
    commission_pct = (total_commission / initial_capital) * 100

    return BacktestResult(
        symbol=symbol,
        strategy_name=strategy_name,
        start=str(prices.index[0].date()),
        end=str(prices.index[-1].date()),
        total_return_pct=total_return,
        buy_hold_return_pct=buy_hold_return,
        max_drawdown_pct=max_drawdown(equity_series),
        num_trades=len(trades),
        commission_cost_pct=commission_pct,
        net_return_pct=total_return,   # commissions already deducted in simulation
        equity_curve=equity_series,
        trades=trades,
    )


def print_results(result: BacktestResult, verbose: bool = False) -> None:
    sep = "─" * 58
    print(f"\n{sep}")
    print(f"  Backtest: {result.symbol}  |  Strategy: {result.strategy_name}")
    print(f"  Period : {result.start} → {result.end}")
    print(sep)
    print(f"  Total return (strategy)   : {result.total_return_pct:+.2f}%")
    print(f"  Buy-and-hold return       : {result.buy_hold_return_pct:+.2f}%")
    print(f"  Max drawdown              : -{result.max_drawdown_pct:.2f}%")
    print(f"  Number of trades          : {result.num_trades}")
    print(f"  Est. commission drag      : -{result.commission_cost_pct:.3f}% "
          f"(@ {config.COMMISSION_BPS} bps/trade)")
    diff = result.total_return_pct - result.buy_hold_return_pct
    print(f"  Alpha vs buy-and-hold     : {diff:+.2f}%")
    print(sep)

    if verbose and result.trades:
        print(f"\n  Trade log ({len(result.trades)} trades):")
        for t in result.trades:
            print(
                f"    {t['date'].strftime('%Y-%m-%d')}  {t['side']:4s}  "
                f"{t['shares']:.4f} sh @ ${t['price']:.2f}  "
                f"(commission ${t['commission']:.4f})"
            )
        print()


def main():
    parser = argparse.ArgumentParser(description="Run a trading strategy backtest")
    parser.add_argument("--symbol", default=config.BACKTEST_SYMBOL)
    parser.add_argument("--start", default=config.BACKTEST_START)
    parser.add_argument("--end", default=config.BACKTEST_END)
    parser.add_argument(
        "--strategy", default=config.STRATEGY,
        choices=["trend_following", "mean_reversion"],
    )
    parser.add_argument("--fast-window", type=int, default=config.TF_FAST_WINDOW)
    parser.add_argument("--slow-window", type=int, default=config.TF_SLOW_WINDOW)
    parser.add_argument("--mr-window", type=int, default=config.MR_WINDOW)
    parser.add_argument("--mr-entry-z", type=float, default=config.MR_ENTRY_Z)
    parser.add_argument("--mr-exit-z", type=float, default=config.MR_EXIT_Z)
    parser.add_argument("--verbose", action="store_true", help="print full trade log")
    parser.add_argument(
        "--compare", action="store_true",
        help="run both strategies and compare",
    )
    args = parser.parse_args()

    strategies_to_run = (
        ["trend_following", "mean_reversion"] if args.compare else [args.strategy]
    )

    for strat in strategies_to_run:
        result = run_backtest(
            symbol=args.symbol,
            start=args.start,
            end=args.end,
            strategy_name=strat,
            fast_window=args.fast_window,
            slow_window=args.slow_window,
            mr_window=args.mr_window,
            mr_entry_z=args.mr_entry_z,
            mr_exit_z=args.mr_exit_z,
        )
        print_results(result, verbose=args.verbose)


if __name__ == "__main__":
    main()
