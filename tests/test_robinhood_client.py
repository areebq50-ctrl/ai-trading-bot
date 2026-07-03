"""
Tests for the Robinhood MCP client's safety gates — no network/auth needed.
These only test the parts that don't require a real MCP connection: token
loading and the DRY_RUN hard block on place_equity_order.
"""
import asyncio
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import config
import robinhood_client
from robinhood_client import RobinhoodAuthError, RobinhoodClient


class TestTokenLoading(unittest.TestCase):
    def setUp(self):
        os.environ.pop("ROBINHOOD_ACCESS_TOKEN", None)
        os.environ.pop("ROBINHOOD_TOKEN_SECRET_NAME", None)

    def tearDown(self):
        os.environ.pop("ROBINHOOD_ACCESS_TOKEN", None)
        os.environ.pop("ROBINHOOD_TOKEN_SECRET_NAME", None)

    def test_no_token_configured(self):
        client = RobinhoodClient()
        self.assertIsNone(client.access_token)

    def test_require_token_raises_without_token(self):
        client = RobinhoodClient()
        with self.assertRaises(RobinhoodAuthError):
            client._require_token()

    def test_env_var_token_loaded(self):
        os.environ["ROBINHOOD_ACCESS_TOKEN"] = "test-token-123"
        client = RobinhoodClient()
        self.assertEqual(client.access_token, "test-token-123")
        self.assertEqual(client._require_token(), "test-token-123")

    def test_explicit_token_takes_precedence(self):
        os.environ["ROBINHOOD_ACCESS_TOKEN"] = "env-token"
        client = RobinhoodClient(access_token="explicit-token")
        self.assertEqual(client.access_token, "explicit-token")


class TestDryRunGuard(unittest.TestCase):
    def setUp(self):
        os.environ["ROBINHOOD_ACCESS_TOKEN"] = "test-token"
        self._orig_dry_run = config.DRY_RUN

    def tearDown(self):
        os.environ.pop("ROBINHOOD_ACCESS_TOKEN", None)
        config.DRY_RUN = self._orig_dry_run

    def test_place_order_blocked_when_dry_run_true(self):
        config.DRY_RUN = True
        client = RobinhoodClient()
        with self.assertRaises(RuntimeError) as ctx:
            asyncio.run(client.place_equity_order("SPY", "buy", 1, "limit", 430.0))
        self.assertIn("DRY_RUN", str(ctx.exception))

    def test_place_order_not_blocked_purely_by_dry_run_when_false(self):
        # DRY_RUN=False should NOT raise the dry-run guard error — it will
        # instead try (and fail, since there's no real MCP server here) on
        # the network call, which is the expected/safe failure mode for a
        # test environment with no real credentials.
        config.DRY_RUN = False
        client = RobinhoodClient()
        with self.assertRaises(Exception) as ctx:
            asyncio.run(client.place_equity_order("SPY", "buy", 1, "limit", 430.0))
        self.assertNotIn("DRY_RUN is True", str(ctx.exception))


if __name__ == "__main__":
    unittest.main(verbosity=2)
