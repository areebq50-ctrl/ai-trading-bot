"""
Durable decision logging — every cycle's decision gets recorded, not just
trades. Writes to DynamoDB in prod; falls back to a local CSV automatically
if DynamoDB isn't reachable/configured (e.g. running locally without AWS
creds), so you can always inspect what the bot is thinking.

Table schema (create with deploy/create_dynamodb_tables.sh):
  Table: trading-bot-logs (partition key: id (S), sort key: timestamp (S))
"""
import csv
import os
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import config

CSV_FALLBACK_PATH = Path(os.environ.get("LOCAL_LOG_CSV", "trade_log.csv"))


@dataclass
class DecisionLogEntry:
    """One row per trading cycle, whether or not a trade happened."""

    symbol: str
    strategy: str
    signal_value: float          # e.g. z-score or fast/slow MA spread
    action: str                  # "BUY" | "SELL" | "HOLD" | "SKIPPED"
    reason: str                  # human-readable why
    quantity: float
    price: float
    resulting_position_shares: float
    account_balance_after: float
    dry_run: bool
    order_id: Optional[str] = None
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()


class DecisionLogger:
    """
    Usage:
        logger = DecisionLogger()
        logger.log(DecisionLogEntry(symbol="SPY", strategy="mean_reversion", ...))

    Set FORCE_CSV_LOG=1 to always use the local CSV even if boto3/DynamoDB
    is available (useful for local testing without touching AWS).
    """

    def __init__(self, table_name: Optional[str] = None, use_dynamodb: bool = True):
        self.table_name = table_name or config.DYNAMODB_LOG_TABLE
        self._table = None
        force_csv = os.environ.get("FORCE_CSV_LOG", "0") == "1"
        if use_dynamodb and not force_csv:
            self._table = self._try_connect_dynamodb()

    def _try_connect_dynamodb(self):
        try:
            import boto3

            table = boto3.resource("dynamodb", region_name=config.AWS_REGION).Table(self.table_name)
            table.load()  # raises if table doesn't exist / no creds / no network
            return table
        except Exception as e:
            print(
                f"[decision_log] WARNING: DynamoDB unavailable ({e!r}) — "
                f"falling back to local CSV at {CSV_FALLBACK_PATH}"
            )
            return None

    def log(self, entry: DecisionLogEntry) -> None:
        if self._table is not None:
            item = {k: str(v) if isinstance(v, float) else v for k, v in asdict(entry).items()}
            item["id"] = str(uuid.uuid4())
            self._table.put_item(Item=item)
        else:
            self._log_csv(entry)

    def _log_csv(self, entry: DecisionLogEntry) -> None:
        row = asdict(entry)
        is_new = not CSV_FALLBACK_PATH.exists()
        with open(CSV_FALLBACK_PATH, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(row.keys()))
            if is_new:
                writer.writeheader()
            writer.writerow(row)
