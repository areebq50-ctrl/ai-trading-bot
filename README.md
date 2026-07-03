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
| Robinhood MCP client (`robinhood_client.py`) | Interface built, safety-tested (6 tests) — **auth unverified, blocked on Robinhood account access** |
| Main bot loop (`main.py`) | Built — two parsing functions are placeholders until there's a real Robinhood connection to inspect real response shapes against |
| AWS deployment | Documented (`deploy/`) — Lightsail always-on instance, not Lambda (see why below) |

**44/44 unit tests pass.** Run them yourself: `python -m pytest tests/ -v`

## What's blocking full live wiring

1. **You don't have Robinhood Agentic Trading access yet.** It's rolling
   out gradually — Robinhood emails you when it's available, and you need a
   primary Robinhood account in good standing first. Nothing below can be
   tested end-to-end until that access arrives.
2. **The auth flow for a headless bot isn't documented by Robinhood.**
   Their docs only cover connecting through a live AI app session (Claude
   Code, Claude Desktop, ChatGPT, Codex, Cursor, Grok). `robinhood_client.py`
   has the MCP call plumbing built and ready, but getting a long-lived
   access token into it needs to be worked out once you have account access
   — see the big comment at the top of that file for the two realistic paths.

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
tests/                  44 unit tests, no AWS/network required
```

## Next steps, in order

1. Run the real-data backtest above and sanity-check the numbers.
2. Wait for / check for Robinhood Agentic Trading access.
3. Once you have access: complete the one-time OAuth login through Claude
   Desktop or Claude Code, and work out how to get a reusable access token
   into `ROBINHOOD_ACCESS_TOKEN` (see `robinhood_client.py`'s docstring).
4. Fix the two placeholder parsers in `main.py` (`_parse_historicals`,
   `_extract_account_value`, `_extract_shares`) against the real response
   shapes you'll see once connected.
5. Run `main.py` locally with `DRY_RUN=true` and watch it log real dry-run
   decisions for a few days.
6. Only then: follow `deploy/README.md` to put it on AWS.
7. Only after that's been running clean in dry-run for a while: flip
   `DRY_RUN=false` yourself, deliberately — never automated.
