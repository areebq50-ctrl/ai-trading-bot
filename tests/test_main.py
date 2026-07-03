"""
Tests for main.py's response parsers, using real fixture data captured from
live get_portfolio / get_equity_positions / get_equity_historicals calls
against the actual Robinhood Agentic account (redacted account numbers) —
not guessed shapes.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from main import _extract_account_value, _extract_shares, _parse_historicals

# ── Real captured fixtures ───────────────────────────────────────────────
REAL_PORTFOLIO_RESPONSE = {
    "data": {
        "total_value": "100",
        "equity_value": "0",
        "options_value": "0",
        "futures_value": "0",
        "event_contracts_value": "0",
        "crypto_value": "0",
        "cash": "100",
        "pending_deposits": "0",
        "mutual_funds_value": "0",
        "fixed_income_value": "0",
        "currency": "USD",
        "buying_power": {
            "buying_power": "100.0000",
            "unleveraged_buying_power": "100.0000",
            "display_currency": "USD",
        },
    }
}

REAL_EMPTY_POSITIONS_RESPONSE = {"data": {"positions": []}}

REAL_POSITIONS_RESPONSE_WITH_HOLDING = {
    "data": {
        "positions": [
            {
                "symbol": "SPY",
                "quantity": "0.067114",
                "shares_available_for_sells": "0.067114",
                "average_buy_price": "745.00",
            }
        ]
    }
}

REAL_HISTORICALS_RESPONSE = {
    "data": {
        "results": [
            {
                "symbol": "SPY",
                "interval": "day",
                "bounds": "regular",
                "bars": [
                    {"begins_at": "2026-06-30T00:00:00Z", "open_price": "741.290000", "close_price": "746.770000", "high_price": "748.020000", "low_price": "740.890000", "volume": 55626035, "session": "reg"},
                    {"begins_at": "2026-07-01T00:00:00Z", "open_price": "745.000000", "close_price": "745.760000", "high_price": "749.435000", "low_price": "742.375000", "volume": 47100878, "session": "reg"},
                    {"begins_at": "2026-07-02T00:00:00Z", "open_price": "747.400000", "close_price": "744.780000", "high_price": "751.310000", "low_price": "740.030000", "volume": 57505953, "session": "reg"},
                ],
            }
        ]
    }
}


class TestExtractAccountValue(unittest.TestCase):
    def test_parses_real_portfolio_shape(self):
        self.assertEqual(_extract_account_value(REAL_PORTFOLIO_RESPONSE), 100.0)

    def test_handles_unwrapped_data_dict_too(self):
        # In case a caller ever passes the already-unwrapped "data" dict.
        self.assertEqual(_extract_account_value(REAL_PORTFOLIO_RESPONSE["data"]), 100.0)


class TestExtractShares(unittest.TestCase):
    def test_empty_positions_returns_zero(self):
        self.assertEqual(_extract_shares(REAL_EMPTY_POSITIONS_RESPONSE, "SPY"), 0.0)

    def test_finds_fractional_holding(self):
        shares = _extract_shares(REAL_POSITIONS_RESPONSE_WITH_HOLDING, "SPY")
        self.assertAlmostEqual(shares, 0.067114, places=6)

    def test_symbol_not_held_returns_zero(self):
        self.assertEqual(_extract_shares(REAL_POSITIONS_RESPONSE_WITH_HOLDING, "QQQ"), 0.0)


class TestParseHistoricals(unittest.TestCase):
    def test_parses_real_bars_shape(self):
        series = _parse_historicals(REAL_HISTORICALS_RESPONSE, "SPY")
        self.assertEqual(len(series), 3)
        self.assertAlmostEqual(series.iloc[-1], 744.78, places=2)
        self.assertAlmostEqual(series.iloc[0], 746.77, places=2)

    def test_series_is_chronologically_ordered(self):
        series = _parse_historicals(REAL_HISTORICALS_RESPONSE, "SPY")
        self.assertTrue(series.index.is_monotonic_increasing)

    def test_missing_symbol_raises_clear_error(self):
        with self.assertRaises(ValueError):
            _parse_historicals(REAL_HISTORICALS_RESPONSE, "NONEXISTENT")


if __name__ == "__main__":
    unittest.main(verbosity=2)
