"""
Tests for the backtest engine and both strategies.
Uses synthetic price data — no network needed.
Run: python -m pytest tests/ -v   or   python tests/test_backtest.py
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import math
import random
import unittest
from datetime import date, timedelta

import pandas as pd

from strategies import get_strategy
from strategies.trend_following import TrendFollowingStrategy
from strategies.mean_reversion import MeanReversionStrategy
from backtest import run_backtest, max_drawdown


def make_prices(n: int, start_price: float = 100.0, seed: int = 42,
                trend: float = 0.0002, vol: float = 0.012) -> pd.Series:
    """Geometric Brownian Motion price series."""
    random.seed(seed)
    prices = [start_price]
    for _ in range(n - 1):
        shock = random.gauss(trend, vol)
        prices.append(prices[-1] * math.exp(shock))
    idx = pd.date_range(start="2020-01-02", periods=n, freq="B")
    return pd.Series(prices, index=idx, name="Close")


def make_mean_reverting_prices(n: int, mean: float = 100.0, seed: int = 7,
                                vol: float = 0.01, reversion: float = 0.05) -> pd.Series:
    """Ornstein-Uhlenbeck-like process."""
    random.seed(seed)
    prices = [mean]
    for _ in range(n - 1):
        p = prices[-1]
        drift = reversion * (mean - p)
        shock = random.gauss(0, vol * mean)
        prices.append(p + drift + shock)
    idx = pd.date_range(start="2020-01-02", periods=n, freq="B")
    return pd.Series(prices, index=idx, name="Close")


class TestMaxDrawdown(unittest.TestCase):
    def test_no_drawdown(self):
        eq = pd.Series([100, 105, 110, 115])
        self.assertAlmostEqual(max_drawdown(eq), 0.0)

    def test_known_drawdown(self):
        eq = pd.Series([100, 120, 60, 90])  # 120 → 60 = 50% drawdown
        self.assertAlmostEqual(max_drawdown(eq), 50.0, places=5)


class TestTrendFollowing(unittest.TestCase):
    def setUp(self):
        self.strategy = TrendFollowingStrategy(fast_window=5, slow_window=20)

    def test_signals_shape(self):
        prices = make_prices(100)
        df = self.strategy.compute_signals(prices)
        self.assertEqual(len(df), 100)
        self.assertIn("signal", df.columns)
        self.assertIn("description", df.columns)

    def test_warmup_period_is_flat(self):
        prices = make_prices(100)
        df = self.strategy.compute_signals(prices)
        # First slow_window-1 rows should be 0 (no signal yet)
        self.assertTrue((df["signal"].iloc[:19] == 0).all())

    def test_signal_values_binary(self):
        prices = make_prices(200)
        df = self.strategy.compute_signals(prices)
        self.assertTrue(df["signal"].isin([0, 1]).all())

    def test_uptrend_generates_long_signal(self):
        # Pure uptrend: fast MA will exceed slow MA
        prices = pd.Series(range(1, 101), dtype=float,
                           index=pd.date_range("2020-01-02", periods=100, freq="B"))
        df = self.strategy.compute_signals(prices)
        # After warmup, all signals should be 1
        self.assertTrue((df["signal"].iloc[20:] == 1).all())

    def test_downtrend_stays_flat(self):
        prices = pd.Series(range(100, 0, -1), dtype=float,
                           index=pd.date_range("2020-01-02", periods=100, freq="B"))
        df = self.strategy.compute_signals(prices)
        self.assertTrue((df["signal"].iloc[20:] == 0).all())


class TestMeanReversion(unittest.TestCase):
    def setUp(self):
        self.strategy = MeanReversionStrategy(window=10, entry_z=-1.0, exit_z=0.0)

    def test_signals_shape(self):
        prices = make_mean_reverting_prices(100)
        df = self.strategy.compute_signals(prices)
        self.assertEqual(len(df), 100)
        self.assertIn("z_score", df.columns)
        self.assertIn("signal", df.columns)

    def test_warmup_period_is_flat(self):
        prices = make_mean_reverting_prices(100)
        df = self.strategy.compute_signals(prices)
        self.assertTrue((df["signal"].iloc[:10] == 0).all())

    def test_signal_values_binary(self):
        prices = make_mean_reverting_prices(200)
        df = self.strategy.compute_signals(prices)
        self.assertTrue(df["signal"].isin([0, 1]).all())

    def test_big_dip_triggers_entry(self):
        # Force a price spike down: mean 100, then sudden 80 = z well below -1
        prices_list = [100.0] * 20 + [70.0] + [100.0] * 30
        prices = pd.Series(prices_list,
                           index=pd.date_range("2020-01-02", periods=51, freq="B"))
        df = self.strategy.compute_signals(prices)
        # After the dip there should be at least one long signal
        self.assertTrue(df["signal"].iloc[20:].any())


class TestBacktestEngine(unittest.TestCase):
    """Integration tests using monkey-patched fetch_prices."""

    def _run(self, prices: pd.Series, strategy_name: str, **kwargs) -> object:
        import backtest as bt
        original = bt.fetch_prices
        bt.fetch_prices = lambda *_a, **_k: prices
        try:
            result = bt.run_backtest(
                symbol="FAKE",
                start="2020-01-01",
                end="2022-12-31",
                strategy_name=strategy_name,
                initial_capital=100.0,
                **kwargs,
            )
        finally:
            bt.fetch_prices = original
        return result

    def test_mean_reversion_result_fields(self):
        prices = make_mean_reverting_prices(252)
        result = self._run(prices, "mean_reversion", mr_window=20)
        self.assertIsNotNone(result.total_return_pct)
        self.assertIsNotNone(result.max_drawdown_pct)
        self.assertGreaterEqual(result.num_trades, 0)
        self.assertEqual(len(result.equity_curve), 252)

    def test_trend_following_result_fields(self):
        prices = make_prices(252)
        result = self._run(prices, "trend_following",
                           fast_window=5, slow_window=20)
        self.assertIsNotNone(result.total_return_pct)
        self.assertGreaterEqual(result.num_trades, 0)

    def test_commission_reduces_return(self):
        """Net return with high commission should be lower than with zero."""
        import backtest as bt
        original = bt.fetch_prices
        prices = make_prices(252, trend=0.0005)  # trending up, will trade
        bt.fetch_prices = lambda *_a, **_k: prices
        try:
            r_free = bt.run_backtest("X", "2020-01-01", "2022-12-31",
                                     "trend_following", fast_window=5, slow_window=20,
                                     commission_bps=0, initial_capital=100.0)
            r_costly = bt.run_backtest("X", "2020-01-01", "2022-12-31",
                                       "trend_following", fast_window=5, slow_window=20,
                                       commission_bps=100, initial_capital=100.0)
        finally:
            bt.fetch_prices = original
        self.assertGreaterEqual(r_free.total_return_pct, r_costly.total_return_pct)

    def test_no_position_exceeds_max_position_pct(self):
        """Each buy should deploy at most MAX_POSITION_PCT of capital."""
        import backtest as bt
        import config
        original = bt.fetch_prices
        prices = make_prices(300, trend=0.001)
        bt.fetch_prices = lambda *_a, **_k: prices
        try:
            result = bt.run_backtest("X", "2020-01-01", "2022-12-31",
                                     "trend_following", fast_window=5, slow_window=20,
                                     initial_capital=100.0)
        finally:
            bt.fetch_prices = original
        for t in result.trades:
            if t["side"] == "BUY":
                deployed = t["shares"] * t["price"]
                capital_before = t["capital_before"]
                # Each buy deploys at most MAX_POSITION_PCT of available capital at that moment
                self.assertLessEqual(deployed, capital_before * config.MAX_POSITION_PCT + 1e-6)

    def test_no_trades_when_warming_up_only(self):
        """If series is shorter than the slow window, no trades should be placed."""
        import backtest as bt
        prices = make_prices(30)  # shorter than default slow_window=50
        original = bt.fetch_prices
        bt.fetch_prices = lambda *_a, **_k: prices
        try:
            result = bt.run_backtest("X", "2020-01-01", "2022-12-31",
                                     "trend_following", slow_window=50, initial_capital=100.0)
        finally:
            bt.fetch_prices = original
        self.assertEqual(result.num_trades, 0)

    def test_buy_hold_return_computed_correctly(self):
        prices = make_prices(100, start_price=100.0)
        import backtest as bt
        original = bt.fetch_prices
        bt.fetch_prices = lambda *_a, **_k: prices
        try:
            result = bt.run_backtest("X", "2020-01-01", "2022-12-31",
                                     "mean_reversion", initial_capital=100.0)
        finally:
            bt.fetch_prices = original
        expected = (prices.iloc[-1] / prices.iloc[0] - 1) * 100
        self.assertAlmostEqual(result.buy_hold_return_pct, expected, places=5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
