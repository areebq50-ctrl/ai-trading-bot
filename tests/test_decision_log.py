"""
Tests for the decision logger's CSV fallback path (no AWS needed).
Forces FORCE_CSV_LOG=1 so these tests never try to reach real DynamoDB.
"""
import csv
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

os.environ["FORCE_CSV_LOG"] = "1"

import decision_log
from decision_log import DecisionLogEntry, DecisionLogger


class TestDecisionLoggerCSVFallback(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.csv_path = Path(self.tmpdir.name) / "trade_log.csv"
        decision_log.CSV_FALLBACK_PATH = self.csv_path
        self.logger = DecisionLogger()

    def tearDown(self):
        self.tmpdir.cleanup()

    def _make_entry(self, **overrides):
        defaults = dict(
            symbol="SPY",
            strategy="mean_reversion",
            signal_value=-1.4,
            action="BUY",
            reason="z-score below entry threshold",
            quantity=0.25,
            price=430.10,
            resulting_position_shares=0.25,
            account_balance_after=50.0,
            dry_run=True,
        )
        defaults.update(overrides)
        return DecisionLogEntry(**defaults)

    def test_creates_csv_with_header_on_first_write(self):
        self.logger.log(self._make_entry())
        self.assertTrue(self.csv_path.exists())
        with open(self.csv_path) as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["symbol"], "SPY")
        self.assertEqual(rows[0]["action"], "BUY")

    def test_appends_without_duplicating_header(self):
        self.logger.log(self._make_entry(action="BUY"))
        self.logger.log(self._make_entry(action="HOLD", quantity=0))
        with open(self.csv_path) as f:
            lines = f.readlines()
        # 1 header + 2 data rows
        self.assertEqual(len(lines), 3)

    def test_logs_skipped_and_hold_actions_not_just_trades(self):
        self.logger.log(self._make_entry(action="SKIPPED", reason="kill switch engaged", quantity=0))
        with open(self.csv_path) as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(rows[0]["action"], "SKIPPED")

    def test_timestamp_auto_populated(self):
        entry = self._make_entry()
        self.assertTrue(entry.timestamp)  # non-empty ISO timestamp set in __post_init__


if __name__ == "__main__":
    unittest.main(verbosity=2)
