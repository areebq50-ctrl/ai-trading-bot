"""
Robinhood Trading MCP client — wraps calls to Robinhood's OFFICIAL Agentic
Trading MCP server (https://agent.robinhood.com/mcp/trading).

STATUS as of the last live check: account access CONFIRMED. A dedicated
Agentic account exists (agentic_allowed=true, cash type, funded with $100,
zero open positions) and read tools (get_accounts, get_portfolio,
get_equity_quotes, get_equity_positions, get_equity_historicals) were called
live and returned real data — the response-shape parsing in main.py is
built against that real data, not guesses.

WHAT'S STILL UNVERIFIED: running this UNATTENDED (e.g. on the AWS Lightsail
box in deploy/) still needs its own access token. The live check above went
through an interactive Cowork session's own connected-apps auth — that
doesn't hand this script a portable bearer token to store in Secrets
Manager. Robinhood's docs only document connecting through a live AI app
session (Claude Code, Claude Desktop, ChatGPT, Codex, Cursor, Grok), so how
a standalone always-on server authenticates is still the open question. See
the module-level TODO in _load_token().

IMPORTANT DESIGN TRADE-OFF DISCOVERED FROM THE REAL TOOL SCHEMA
------------------------------------------------------------------
Robinhood's place_equity_order / review_equity_order docs state fractional
shares are only allowed on type="market" orders during regular market hours
— never on limit orders. SPY trades around $745/share; with MAX_CAPITAL=100
and MAX_POSITION_PCT=0.5, a single position is capped at $50 — nowhere near
one whole share. That means BUY orders on SPY must be dollar-based market
orders (fractional), not limit orders, or the bot literally cannot buy
anything with this budget. This directly conflicts with the original
brief's "use limit orders, not blind market orders" preference. This file
implements market orders for fractional buys and documents the risk
(no price-protection on fills) rather than silently picking a side —
flag this to the user before going live.
"""
import json
import os
import sys
from typing import Any, Optional

import config

MCP_URL = "https://agent.robinhood.com/mcp/trading"


class RobinhoodAuthError(RuntimeError):
    """Raised when no usable access token is configured."""


class RobinhoodClient:
    def __init__(self, access_token: Optional[str] = None):
        self.access_token = access_token or self._load_token()
        self._agentic_account_number: Optional[str] = None

        if not config.DRY_RUN:
            print(
                "\n"
                "╔══════════════════════════════════════════════════════════╗\n"
                "║  WARNING: DRY_RUN IS FALSE — LIVE ORDERS WILL BE PLACED   ║\n"
                "║  Real money in your Robinhood Agentic account is at risk. ║\n"
                "╚══════════════════════════════════════════════════════════╝\n",
                file=sys.stderr,
            )

    # ── Token loading ────────────────────────────────────────────────────
    def _load_token(self) -> Optional[str]:
        """
        Load order of precedence:
          1. ROBINHOOD_ACCESS_TOKEN env var (local/dev testing)
          2. AWS Secrets Manager secret named by ROBINHOOD_TOKEN_SECRET_NAME
        Returns None if neither is set — callers should fail loudly rather
        than silently proceeding without auth.

        TODO once you're ready to deploy unattended: this is still the
        unresolved piece. Realistic paths to an actual token: check whether
        Claude Desktop's Robinhood connector exposes the underlying bearer
        token anywhere inspectable, or watch for Robinhood publishing a
        headless/service-account auth flow as the product matures out of
        beta. Don't guess — an invalid token fails loudly, which is safe;
        a *silently wrong* one is the failure mode to avoid.
        """
        env_token = os.environ.get("ROBINHOOD_ACCESS_TOKEN")
        if env_token:
            return env_token

        secret_name = os.environ.get("ROBINHOOD_TOKEN_SECRET_NAME")
        if secret_name:
            try:
                import boto3

                client = boto3.client("secretsmanager", region_name=config.AWS_REGION)
                resp = client.get_secret_value(SecretId=secret_name)
                return resp["SecretString"]
            except Exception as e:
                print(f"[robinhood_client] could not load token from Secrets Manager: {e!r}")
        return None

    def _require_token(self) -> str:
        if not self.access_token:
            raise RobinhoodAuthError(
                "No Robinhood access token configured. Set ROBINHOOD_ACCESS_TOKEN "
                "(dev) or ROBINHOOD_TOKEN_SECRET_NAME (prod, via Secrets Manager). "
                "See the module docstring — unattended auth is still unresolved."
            )
        return self.access_token

    # ── MCP call plumbing ───────────────────────────────────────────────
    async def _call_tool(self, name: str, arguments: dict) -> dict:
        """
        Calls a tool on the Robinhood Trading MCP over streamable HTTP and
        returns the parsed JSON payload (a plain dict), matching the shape
        you'd see calling the same tool interactively.
        """
        token = self._require_token()
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client

        headers = {"Authorization": f"Bearer {token}"}
        async with streamable_http_client(MCP_URL, headers=headers) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(name, arguments)
                if result.content and hasattr(result.content[0], "text"):
                    return json.loads(result.content[0].text)
                return result  # unexpected shape — let callers see the raw object

    # ── Account helpers ──────────────────────────────────────────────────
    async def get_accounts(self) -> dict:
        return await self._call_tool("get_accounts", {})

    async def get_agentic_account_number(self) -> str:
        """Finds and caches the account_number of the agentic_allowed=true
        account — the only one this bot is permitted to trade in."""
        if self._agentic_account_number:
            return self._agentic_account_number
        accounts = await self.get_accounts()
        for acct in accounts.get("data", {}).get("accounts", []):
            if acct.get("agentic_allowed"):
                self._agentic_account_number = acct["account_number"]
                return self._agentic_account_number
        raise RuntimeError(
            "No agentic_allowed=true account found. Open/confirm your Robinhood "
            "Agentic account before running the bot."
        )

    # ── Read-only tools (safe in any mode) ─────────────────────────────
    async def get_portfolio(self, account_number: str) -> dict:
        return await self._call_tool("get_portfolio", {"account_number": account_number})

    async def get_equity_quotes(self, symbols: list[str]) -> dict:
        return await self._call_tool("get_equity_quotes", {"symbols": symbols})

    async def get_equity_positions(self, account_number: str) -> dict:
        return await self._call_tool("get_equity_positions", {"account_number": account_number})

    async def get_equity_tradability(self, account_number: str, symbols: list[str]) -> dict:
        return await self._call_tool(
            "get_equity_tradability", {"account_number": account_number, "symbols": symbols}
        )

    async def get_equity_historicals(self, symbols: list[str], start_time: str, interval: str = "day") -> dict:
        return await self._call_tool(
            "get_equity_historicals",
            {"symbols": symbols, "start_time": start_time, "interval": interval},
        )

    # ── Order tools — DRY_RUN gated ─────────────────────────────────────
    async def review_equity_order(
        self,
        account_number: str,
        symbol: str,
        side: str,
        order_type: str,
        quantity: Optional[str] = None,
        dollar_amount: Optional[str] = None,
        limit_price: Optional[str] = None,
    ) -> dict:
        """Simulates an order (Robinhood confirms this doesn't place a real
        order) — always safe to call, used in dry-run mode to show what
        WOULD happen. Provide exactly one of quantity or dollar_amount."""
        args = {"account_number": account_number, "symbol": symbol, "side": side, "type": order_type}
        if quantity is not None:
            args["quantity"] = quantity
        if dollar_amount is not None:
            args["dollar_amount"] = dollar_amount
        if limit_price is not None:
            args["limit_price"] = limit_price
        return await self._call_tool("review_equity_order", args)

    async def place_equity_order(
        self,
        account_number: str,
        symbol: str,
        side: str,
        order_type: str,
        quantity: Optional[str] = None,
        dollar_amount: Optional[str] = None,
        limit_price: Optional[str] = None,
        ref_id: Optional[str] = None,
    ) -> dict:
        """
        Places a REAL order. Hard-blocked unless config.DRY_RUN is False —
        this is the one function in the whole codebase that can move money,
        and it refuses to run in dry-run mode no matter what calls it.
        """
        if config.DRY_RUN:
            raise RuntimeError(
                "place_equity_order() called while DRY_RUN is True — refusing. "
                "This should never happen; the bot's main loop should only call "
                "review_equity_order() in dry-run mode. This is a bug if you see it."
            )
        args = {"account_number": account_number, "symbol": symbol, "side": side, "type": order_type}
        if quantity is not None:
            args["quantity"] = quantity
        if dollar_amount is not None:
            args["dollar_amount"] = dollar_amount
        if limit_price is not None:
            args["limit_price"] = limit_price
        if ref_id is not None:
            args["ref_id"] = ref_id
        return await self._call_tool("place_equity_order", args)

    async def cancel_equity_order(self, account_number: str, order_id: str) -> dict:
        return await self._call_tool(
            "cancel_equity_order", {"account_number": account_number, "order_id": order_id}
        )
