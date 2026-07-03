"""
Central configuration. All tunables live here.
Secrets (API keys, AWS creds) must come from environment variables or Secrets Manager — never hardcode.
"""
import os

# ── Safety ────────────────────────────────────────────────────────────────────
DRY_RUN: bool = os.environ.get("DRY_RUN", "true").lower() != "false"

# ── Capital limits ─────────────────────────────────────────────────────────────
MAX_CAPITAL: float = float(os.environ.get("MAX_CAPITAL", "100"))          # dollars
MAX_POSITION_PCT: float = float(os.environ.get("MAX_POSITION_PCT", "0.5"))
DAILY_LOSS_HALT_PCT: float = float(os.environ.get("DAILY_LOSS_HALT_PCT", "0.1"))

# ── Strategy ───────────────────────────────────────────────────────────────────
STRATEGY: str = os.environ.get("STRATEGY", "mean_reversion")   # or "trend_following"
SYMBOLS: list[str] = os.environ.get("SYMBOLS", "SPY").split(",")

# trend_following params
TF_FAST_WINDOW: int = int(os.environ.get("TF_FAST_WINDOW", "10"))
TF_SLOW_WINDOW: int = int(os.environ.get("TF_SLOW_WINDOW", "50"))

# mean_reversion params
MR_WINDOW: int = int(os.environ.get("MR_WINDOW", "20"))
MR_ENTRY_Z: float = float(os.environ.get("MR_ENTRY_Z", "-1.2"))
MR_EXIT_Z: float = float(os.environ.get("MR_EXIT_Z", "0.0"))

# ── Scheduling ─────────────────────────────────────────────────────────────────
RUN_INTERVAL_MINUTES: int = int(os.environ.get("RUN_INTERVAL_MINUTES", "60"))

# ── Kill switch ────────────────────────────────────────────────────────────────
# Bot checks this env var every cycle; set KILL_SWITCH=1 to halt without redeploy.
# Can also be a DynamoDB item or S3 flag — see risk.py.
KILL_SWITCH_ENV: str = "KILL_SWITCH"

# ── AWS / notifications ────────────────────────────────────────────────────────
AWS_REGION: str = os.environ.get("AWS_REGION", "us-east-1")
SNS_TOPIC_ARN: str = os.environ.get("SNS_TOPIC_ARN", "")
DYNAMODB_LOG_TABLE: str = os.environ.get("DYNAMODB_LOG_TABLE", "trading-bot-logs")
DYNAMODB_STATE_TABLE: str = os.environ.get("DYNAMODB_STATE_TABLE", "trading-bot-state")

# ── Robinhood MCP ──────────────────────────────────────────────────────────────
# Credentials loaded from env; never put real values here.
ROBINHOOD_CLIENT_ID: str = os.environ.get("ROBINHOOD_CLIENT_ID", "")
ROBINHOOD_CLIENT_SECRET: str = os.environ.get("ROBINHOOD_CLIENT_SECRET", "")

# ── Backtest ───────────────────────────────────────────────────────────────────
BACKTEST_START: str = os.environ.get("BACKTEST_START", "2020-01-01")
BACKTEST_END: str = os.environ.get("BACKTEST_END", "2023-12-31")
BACKTEST_SYMBOL: str = os.environ.get("BACKTEST_SYMBOL", "SPY")
COMMISSION_BPS: float = float(os.environ.get("COMMISSION_BPS", "5"))  # basis points per trade
