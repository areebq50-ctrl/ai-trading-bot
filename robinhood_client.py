"""
Robinhood Trading MCP client — wraps calls to Robinhood's OFFICIAL Agentic
Trading MCP server (https://agent.robinhood.com/mcp/trading).

╔══════════════════════════════════════════════════════════════════════════╗
║  STATUS: interface complete, AUTH UNVERIFIED. Do not treat this as        ║
║  working until you've confirmed the auth flow yourself — see the big      ║
║  comment block below "WHY THIS IS UNVERIFIED".                            ║
╚══════════════════════════════════════════════════════════════════════════╝

WHY THIS IS UNVERIFIED
-----------------------
As of writing, Robinhood's own docs only document connecting the Trading MCP
through a named list of interactive AI platforms — Claude Code, Claude
Desktop, ChatGPT, Codex, Cursor, Grok — each of which drives its own OAuth
login popup and stores the resulting session itself. Robinhood has not
published a spec for registering an arbitrary third-party headless client
(client_id, redirect URI, token endpoint, refresh-token grant, etc.).

That means the *transport* below (MCP over streamable HTTP, bearer token
auth) is standard and should work once you have a valid access token — but
HOW you legally/reliably obtain and refresh that token outside one of the
named platforms is not documented. Two realistic options once you have
account access:

  1. Complete the one-time login through Claude Desktop or Claude Code
     (whichever you have open), then inspect whether the resulting session
     can be reused by this script (e.g. Claude Desktop's MCP connector may
     store a token you can reference, or the OAuth callback may hand back a
     bearer token you can capture once and store in Secrets Manager).
  2. Contact Robinhood support / check for updated developer docs — this
     product is described as "currently rolling out," so the third-party
     headless story may become clearer as it matures out of beta.

Do NOT wire this into a scheduled AWS job until step 1 or 2 above has
actually produced a working, refreshable token in your hands. Wishful
placeholder credentials will just fail loudly (which is the safe outcome —
better than silently not trading, or worse, crashing mid-cycle).

WHAT IS SAFE REGARDLESS
------------------------
Every method below respects config.DRY_RUN. In dry-run mode this client
NEVER calls place_equity_order — it only ever reads data and (for orders)
calls review_equity_order, which Robinhood's docs describe as a pre-trade
simulation that does not place a real order. So even once auth is wired up,
running with DRY_RUN=true (the default) cannot move real money.
"""
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
        self._session = None  # lazily-created MCP ClientSession

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
                "See the module docstring — the auth flow needs to be verified "
                "manually before this will work."
            )
        return self.access_token

    # ── MCP call plumbing ───────────────────────────────────────────────
    async def _call_tool(self, name: str, arguments: dict) -> Any:
        """
        Calls a tool on the Robinhood Trading MCP over streamable HTTP,
        using the loaded bearer token. Requires the `mcp` package
        (see requirements.txt).
        """
        token = self._require_token()
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client

        headers = {"Authorization": f"Bearer {token}"}
        async with streamable_http_client(MCP_URL, headers=headers) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(name, arguments)
                return result

    # ── Read-only tools (safe in any mode) ─────────────────────────────
    async def get_accounts(self) -> Any:
        return await self._call_tool("get_accounts", {})

    async def get_portfolio(self) -> Any:
        return await self._call_tool("get_portfolio", {})

    async def get_equity_quotes(self, symbols: list[str]) -> Any:
        return await self._call_tool("get_equity_quotes", {"symbols": symbols})

    async def get_equity_positions(self) -> Any:
        return await self._call_tool("get_equity_positions", {})

    async def get_equity_tradability(self, symbol: str) -> Any:
        return await self._call_tool("get_equity_tradability", {"symbol": symbol})

    async def get_equity_historicals(self, symbol: str, lookback_days: int) -> Any:
        return await self._call_tool(
            "get_equity_historicals", {"symbol": symbol, "lookback_days": lookback_days}
        )

    # ── Order tools — DRY_RUN gated ─────────────────────────────────────
    async def review_equity_order(
        self, symbol: str, side: str, quantity: float, order_type: str, limit_price: Optional[float] = None
    ) -> Any:
        """Simulates an order and returns pre-trade warnings. Always safe
        to call — Robinhood describes this tool as a simulation, not a
        real order — used in dry-run mode to show what WOULD happen."""
        args = {"symbol": symbol, "side": side, "quantity": quantity, "order_type": order_type}
        if limit_price is not None:
            args["limit_price"] = limit_price
        return await self._call_tool("review_equity_order", args)

    async def place_equity_order(
        self, symbol: str, side: str, quantity: float, order_type: str = "limit", limit_price: Optional[float] = None
    ) -> Any:
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
        args = {"symbol": symbol, "side": side, "quantity": quantity, "order_type": order_type}
        if limit_price is not None:
            args["limit_price"] = limit_price
        return await self._call_tool("place_equity_order", args)

    async def cancel_equity_order(self, order_id: str) -> Any:
        return await self._call_tool("cancel_equity_order", {"order_id": order_id})
