# AI Trading Bot

A small, rule-based trading bot for a dedicated $100 Robinhood Agentic
Trading account. Defaults to dry-run (logs what it would trade, places no
real orders) until you explicitly flip it live.

## Status

| Piece | Status |
|---|---|
| Repo structure, `.gitignore` | Done |
| Backtest engine + both strategies | Done, unit-tested (17 tests). Real-data run needs to happen on a machine with internet access (see below) |
| Risk guardrails (`risk.py`) | Done, unit-tested (17 tests) |
| Decision logging (`decision_log.py`) | Done, unit-tested (4 tests) — DynamoDB with local CSV fallback |
| Robinhood account access | **Confirmed live** — dedicated Agentic account `923108740`, funded with $100, zero open positions |
| Robinhood MCP client (`robinhood_client.py`) | Built and verified against real live calls (get_accounts, get_portfolio, get_equity_quotes, get_equity_positions, get_equity_historicals) — 9 tests. **Unattended/AWS auth still unresolved** (see below) |
| Main bot loop (`main.py`) | Built, parsers verified against real captured response shapes — 8 tests |
| AWS deployment | Documented (`deploy/`) — Lightsail always-on instance, not Lambda (see why below) |

**55/55 unit tests pass.** Run them yourself: `python -m pytest tests/ -v`

## What's actually confirmed vs. still open

Confirmed live, via an interactive Cowork session's own Robinhood connection:
- You have Agentic Trading access and a funded ($100), empty Agentic account.
- The real response shapes for account/portfolio/position/quote/historical
  data — `main.py`'s parsers are built against real data, not guesses.
- A real trade-off the docs didn't make obvious: Robinhood only allows
  **fractional shares on market orders**, never limit orders. SPY trades
  around $745/share, so a $50 position is inherently fractional — meaning
  BUY orders here have to be dollar-based market orders, not limit orders.
  That's a deliberate deviation from the original "always use limit orders"
  preference; see `robinhood_client.py`'s docstring for the full reasoning.

Still open:
- **Unattended auth.** The live check above went through an interactive
  session's own connected-apps login — it did not hand this script a
  portable, storable bearer token. Robinhood's docs only document
  connecting through a live AI app session (Claude Code, Claude Desktop,
  ChatGPT, Codex, Cursor, Grok). How a standalone AWS box authenticates
  without one of those open is still unresolved — see the TODO in
  `robinhood_client.py`'s `_load_token()`.

## Running the backtest with real data

This sandbox's network doesn't reach Yahoo Finance, so I could only verify
the backtest engine against synthetic data (`python backtest_demo.py`) —
mechanically correct, but not real numbers. Run this yourself on a machine
with normal internet access:

```bash
pip install -r requirements.txt
python backtest.py --symbol SPY --start 2020-01-01 --end 2023-12-31 --compare
python backtest.py --symbol SPY --start 2022-01-01 --end 2022-12-31 --compare   # a real down year
```

## Project layout

```
config.py            All tunables (DRY_RUN, MAX_CAPITAL, strategy params, etc.)
backtest.py           Backtest engine (real yfinance data)
backtest_demo.py       Same engine, synthetic data — no network needed
strategies/            trend_following.py, mean_reversion.py — pluggable
risk.py                Kill switch, position sizing, daily loss halt
decision_log.py        Durable per-cycle logging (DynamoDB + CSV fallback)
robinhood_client.py     MCP client wrapper for the Robinhood Trading MCP
main.py                 Ties it all together into one trading cycle
deploy/                 AWS Lightsail setup (systemd timer, DynamoDB tables, SNS)
tests/                  55 unit tests, no AWS/network required
```

## Next steps, in order

1. Run the real-data backtest above and sanity-check the numbers.
2. Work out unattended auth (the one open item above) — likely means
   digging into whether Claude Desktop's Robinhood connector exposes a
   reusable token, or checking Robinhood's docs periodically as the
   product matures out of beta.
3. Once you have a token: run `main.py` locally with `DRY_RUN=true` (set
   `ROBINHOOD_ACCESS_TOKEN` in your shell) and watch it log real dry-run
   decisions for a few days.
4. Only then: follow `deploy/README.md` to put it on AWS.
5. Only after that's been running clean in dry-run for a while: flip
   `DRY_RUN=false` yourself, deliberately — never automated.
