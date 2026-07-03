"""
Tests for the risk guardrail module. No AWS or network needed — uses
temp files for kill-switch and daily-loss state.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import config
from risk import DailyLossHalt, KillSwitch, PositionSizer, RiskManager


class TestKillSwitch(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.flag_path = Path(self.tmpdir.name) / "kill_switch.flag"
        os.environ.pop(config.KILL_SWITCH_ENV, None)

    def tearDown(self):
        self.tmpdir.cleanup()
        os.environ.pop(config.KILL_SWITCH_ENV, None)

    def test_not_engaged_by_default(self):
        ks = KillSwitch(flag_path=str(self.flag_path))
        engaged, _ = ks.is_engaged()
        self.assertFalse(engaged)

    def test_engaged_via_env_var(self):
        os.environ[config.KILL_SWITCH_ENV] = "1"
        ks = KillSwitch(flag_path=str(self.flag_path))
        engaged, reason = ks.is_engaged()
        self.assertTrue(engaged)
        self.assertIn("env var", reason)

    def test_engaged_via_flag_file(self):
        self.flag_path.touch()
        ks = KillSwitch(flag_path=str(self.flag_path))
        engaged, reason = ks.is_engaged()
        self.assertTrue(engaged)
        self.assertIn("flag file", reason)


class TestPositionSizer(unittest.TestCase):
    def test_order_within_limit(self):
        result = PositionSizer.check_order_size(order_dollars=40, current_capital=100)
        self.assertTrue(result.allowed)

    def test_order_exceeds_limit(self):
        # MAX_POSITION_PCT default is 0.5 -> cap is $50 on $100 capital
        result = PositionSizer.check_order_size(order_dollars=60, current_capital=100)
        self.assertFalse(result.allowed)
        self.assertIn("exceeds", result.reason)

    def test_order_exactly_at_limit_allowed(self):
        result = PositionSizer.check_order_size(order_dollars=50, current_capital=100)
        self.assertTrue(result.allowed)


class TestDailyLossHalt(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.state_path = Path(self.tmpdir.name) / "daily_state.json"

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_no_baseline_allows_trading(self):
        dlh = DailyLossHalt(state_path=str(self.state_path))
        result = dlh.check(current_value=90)
        self.assertTrue(result.allowed)

    def test_records_baseline_only_once(self):
        dlh = DailyLossHalt(state_path=str(self.state_path))
        dlh.record_start_of_day(100)
        dlh.record_start_of_day(200)  # should be ignored — already recorded today
        self.assertEqual(dlh.start_of_day_value(), 100)

    def test_within_limit_allows_trading(self):
        dlh = DailyLossHalt(state_path=str(self.state_path))
        dlh.record_start_of_day(100)
        result = dlh.check(current_value=95)  # down 5%, limit is 10%
        self.assertTrue(result.allowed)

    def test_exceeds_limit_halts_trading(self):
        dlh = DailyLossHalt(state_path=str(self.state_path))
        dlh.record_start_of_day(100)
        result = dlh.check(current_value=85)  # down 15%, limit is 10%
        self.assertFalse(result.allowed)
        self.assertIn("DAILY LOSS HALT", result.reason)

    def test_exactly_at_limit_halts_trading(self):
        dlh = DailyLossHalt(state_path=str(self.state_path))
        dlh.record_start_of_day(100)
        result = dlh.check(current_value=90)  # exactly -10%
        self.assertFalse(result.allowed)


class TestRiskManager(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.flag_path = Path(self.tmpdir.name) / "kill_switch.flag"
        self.state_path = Path(self.tmpdir.name) / "daily_state.json"
        self.rm = RiskManager(
            kill_switch=KillSwitch(flag_path=str(self.flag_path)),
            daily_loss=DailyLossHalt(state_path=str(self.state_path)),
        )
        os.environ.pop(config.KILL_SWITCH_ENV, None)

    def tearDown(self):
        self.tmpdir.cleanup()
        os.environ.pop(config.KILL_SWITCH_ENV, None)

    def test_pre_cycle_check_passes_normally(self):
        result = self.rm.pre_cycle_check(account_value=100)
        self.assertTrue(result.allowed)

    def test_pre_cycle_check_blocked_by_kill_switch(self):
        self.flag_path.touch()
        result = self.rm.pre_cycle_check(account_value=100)
        self.assertFalse(result.allowed)
        self.assertIn("KILL SWITCH", result.reason)

    def test_pre_cycle_check_blocked_by_daily_loss(self):
        self.rm.pre_cycle_check(account_value=100)  # records baseline = 100
        result = self.rm.pre_cycle_check(account_value=85)  # down 15%
        self.assertFalse(result.allowed)

    def test_check_order_rejects_over_max_capital(self):
        result = self.rm.check_order(order_dollars=150, current_capital=100)
        self.assertFalse(result.allowed)
        self.assertIn("MAX_CAPITAL", result.reason)

    def test_check_order_rejects_over_position_pct(self):
        result = self.rm.check_order(order_dollars=60, current_capital=100)
        self.assertFalse(result.allowed)

    def test_check_order_allows_valid_order(self):
        result = self.rm.check_order(order_dollars=40, current_capital=100)
        self.assertTrue(result.allowed)


if __name__ == "__main__":
    unittest.main(verbosity=2)
