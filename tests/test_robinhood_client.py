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
            asyncio.run(
                client.place_equity_order(
                    account_number="923108740",
                    symbol="SPY",
                    side="buy",
                    order_type="market",
                    dollar_amount="50.00",
                )
            )
        self.assertIn("DRY_RUN", str(ctx.exception))

    def test_place_order_not_blocked_purely_by_dry_run_when_false(self):
        # DRY_RUN=False should NOT raise the dry-run guard error — it will
        # instead try (and fail, since there's no real MCP server here) on
        # the network call, which is the expected/safe failure mode for a
        # test environment with no real credentials.
        config.DRY_RUN = False
        client = RobinhoodClient()
        with self.assertRaises(Exception) as ctx:
            asyncio.run(
                client.place_equity_order(
                    account_number="923108740",
                    symbol="SPY",
                    side="buy",
                    order_type="market",
                    dollar_amount="50.00",
                )
            )
        self.assertNotIn("DRY_RUN is True", str(ctx.exception))


class TestAgenticAccountLookup(unittest.TestCase):
    """Uses real fixture data captured from a live get_accounts call
    (account numbers are the actual ones from the connected account)."""

    REAL_ACCOUNTS_RESPONSE = {
        "data": {
            "accounts": [
                {"account_number": "754810547", "type": "margin", "agentic_allowed": False, "is_default": True},
                {"account_number": "923108740", "type": "cash", "nickname": "Agentic", "agentic_allowed": True, "is_default": False},
            ]
        }
    }

    def setUp(self):
        os.environ["ROBINHOOD_ACCESS_TOKEN"] = "test-token"

    def tearDown(self):
        os.environ.pop("ROBINHOOD_ACCESS_TOKEN", None)

    def test_finds_agentic_allowed_account(self):
        client = RobinhoodClient()

        async def fake_get_accounts():
            return self.REAL_ACCOUNTS_RESPONSE

        client.get_accounts = fake_get_accounts
        account_number = asyncio.run(client.get_agentic_account_number())
        self.assertEqual(account_number, "923108740")

    def test_caches_result_after_first_call(self):
        client = RobinhoodClient()
        call_count = 0

        async def fake_get_accounts():
            nonlocal call_count
            call_count += 1
            return self.REAL_ACCOUNTS_RESPONSE

        client.get_accounts = fake_get_accounts
        asyncio.run(client.get_agentic_account_number())
        asyncio.run(client.get_agentic_account_number())
        self.assertEqual(call_count, 1)

    def test_raises_if_no_agentic_account(self):
        client = RobinhoodClient()

        async def fake_get_accounts():
            return {"data": {"accounts": [{"account_number": "1", "agentic_allowed": False}]}}

        client.get_accounts = fake_get_accounts
        with self.assertRaises(RuntimeError):
            asyncio.run(client.get_agentic_account_number())


if __name__ == "__main__":
    unittest.main(verbosity=2)
