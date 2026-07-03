"""
Demo backtest using synthetic price data (no network needed).
Demonstrates the same output you'll see when running backtest.py with real yfinance data.
Delete this file once you have network access and can run backtest.py directly.
"""
import math
import random
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

import pandas as pd
from backtest import run_backtest, print_results

def make_prices(n, start=100.0, seed=42, trend=0.0002, vol=0.012):
    random.seed(seed)
    prices = [start]
    for _ in range(n - 1):
        prices.append(prices[-1] * math.exp(random.gauss(trend, vol)))
    return pd.Series(prices, index=pd.date_range("2020-01-02", periods=n, freq="B"))

def make_bear_prices(n, start=100.0, seed=42):
    """2022-style bear market: slow grind down with high volatility."""
    return make_prices(n, start=start, seed=seed, trend=-0.0003, vol=0.018)

import backtest as bt

# ── Scenario 1: Bull market 2020-2023 equivalent ──────────────────────────────
print("\n" + "=" * 60)
print("  DEMO MODE — synthetic prices (real run needs network)")
print("=" * 60)

prices_bull = make_prices(756, trend=0.0003, vol=0.012)  # ~3y bull
bt.fetch_prices = lambda *a, **k: prices_bull
for strat in ["mean_reversion", "trend_following"]:
    r = run_backtest("SPY-synthetic", "2020-01-01", "2023-12-31", strat,
                     fast_window=10, slow_window=50, mr_window=20,
                     mr_entry_z=-1.2, mr_exit_z=0.0, initial_capital=100.0)
    print_results(r)

# ── Scenario 2: Bear market (down year) ───────────────────────────────────────
prices_bear = make_bear_prices(252)
bt.fetch_prices = lambda *a, **k: prices_bear
for strat in ["mean_reversion", "trend_following"]:
    r = run_backtest("SPY-bear-synthetic", "2022-01-01", "2022-12-31", strat,
                     fast_window=10, slow_window=50, mr_window=20,
                     mr_entry_z=-1.2, mr_exit_z=0.0, initial_capital=100.0)
    print_results(r)

print("\nTo run against REAL market data from your local machine:")
print("  pip install yfinance")
print("  python backtest.py --symbol SPY --start 2020-01-01 --end 2023-12-31 --compare")
print("  python backtest.py --symbol SPY --start 2022-01-01 --end 2022-12-31 --compare")
print("  python backtest.py --symbol QQQ --start 2020-01-01 --end 2023-12-31 --compare")
