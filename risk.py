"""
Risk guardrail module — hard-coded limits, not suggestions.

Standalone and testable: nothing here needs AWS or Robinhood to run.
The bot calls RiskManager.pre_cycle_check() before doing anything else each
cycle, and RiskManager.check_order() before every single order.
"""
import json
import os
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

import config


@dataclass
class RiskCheckResult:
    allowed: bool
    reason: str

    def __bool__(self) -> bool:
        return self.allowed


class KillSwitch:
    """
    Checked before every trading cycle. Flip it without redeploying anything:
      - set env var KILL_SWITCH=1, or
      - create the flag file (touch kill_switch.flag next to the bot, or the
        equivalent path in S3/EFS if running on AWS)
    Either one halts the bot immediately on its next cycle.
    """

    def __init__(self, flag_path: Optional[str] = None):
        self.flag_path = Path(flag_path or os.environ.get("KILL_SWITCH_FILE", "kill_switch.flag"))

    def is_engaged(self) -> tuple[bool, str]:
        if os.environ.get(config.KILL_SWITCH_ENV, "0") == "1":
            return True, f"{config.KILL_SWITCH_ENV} env var is set to 1"
        if self.flag_path.exists():
            return True, f"kill switch flag file present at {self.flag_path}"
        return False, ""


class PositionSizer:
    """Caps how much capital can go into a single symbol at once."""

    @staticmethod
    def max_position_dollars(current_capital: float) -> float:
        return current_capital * config.MAX_POSITION_PCT

    @staticmethod
    def check_order_size(order_dollars: float, current_capital: float) -> RiskCheckResult:
        cap = PositionSizer.max_position_dollars(current_capital)
        if order_dollars > cap + 1e-9:
            return RiskCheckResult(
                False,
                f"order size ${order_dollars:.2f} exceeds MAX_POSITION_PCT cap "
                f"(${cap:.2f} = {config.MAX_POSITION_PCT:.0%} of ${current_capital:.2f} capital)",
            )
        return RiskCheckResult(True, "within position size limit")


class DailyLossHalt:
    """
    Records account value at the start of each trading day and halts new
    trades for the rest of that day if the account has dropped more than
    DAILY_LOSS_HALT_PCT versus that starting value.

    State is persisted to a small local JSON file by default — swap
    `state_path` for a path backed by the DynamoDB state table in prod so it
    survives across Lambda/instance restarts.
    """

    def __init__(self, state_path: Optional[str] = None):
        self.state_path = Path(state_path or os.environ.get("DAILY_STATE_FILE", "daily_state.json"))

    def _load(self) -> dict:
        if self.state_path.exists():
            return json.loads(self.state_path.read_text())
        return {}

    def _save(self, state: dict) -> None:
        self.state_path.write_text(json.dumps(state))

    def start_of_day_value(self, today: Optional[date] = None) -> Optional[float]:
        today = today or datetime.now(timezone.utc).date()
        return self._load().get(str(today))

    def record_start_of_day(self, account_value: float, today: Optional[date] = None) -> None:
        """No-op if today's baseline was already recorded — only the first
        value of the day counts as the baseline."""
        today = today or datetime.now(timezone.utc).date()
        state = self._load()
        key = str(today)
        if key not in state:
            state[key] = account_value
            self._save(state)

    def check(self, current_value: float, today: Optional[date] = None) -> RiskCheckResult:
        start_value = self.start_of_day_value(today)
        if not start_value or start_value <= 0:
            return RiskCheckResult(True, "no start-of-day baseline recorded yet")
        drawdown = (start_value - current_value) / start_value
        if drawdown >= config.DAILY_LOSS_HALT_PCT:
            return RiskCheckResult(
                False,
                f"DAILY LOSS HALT triggered: account down {drawdown:.2%} vs "
                f"start-of-day value ${start_value:.2f} "
                f"(limit is {config.DAILY_LOSS_HALT_PCT:.0%})",
            )
        return RiskCheckResult(True, f"daily P&L within limit ({drawdown:+.2%} vs start of day)")


class RiskManager:
    """Single entry point the bot's main loop calls each cycle."""

    def __init__(
        self,
        kill_switch: Optional[KillSwitch] = None,
        daily_loss: Optional[DailyLossHalt] = None,
    ):
        self.kill_switch = kill_switch or KillSwitch()
        self.daily_loss = daily_loss or DailyLossHalt()

    def pre_cycle_check(self, account_value: float) -> RiskCheckResult:
        """Call once at the start of every trading cycle, before reading
        prices or computing signals. Blocks the whole cycle if it fails."""
        engaged, reason = self.kill_switch.is_engaged()
        if engaged:
            return RiskCheckResult(False, f"KILL SWITCH engaged: {reason}")
        self.daily_loss.record_start_of_day(account_value)
        return self.daily_loss.check(account_value)

    def check_order(self, order_dollars: float, current_capital: float) -> RiskCheckResult:
        """Call before placing (or dry-run-logging) any single order."""
        if order_dollars > config.MAX_CAPITAL + 1e-9:
            return RiskCheckResult(
                False,
                f"order (${order_dollars:.2f}) exceeds MAX_CAPITAL (${config.MAX_CAPITAL})",
            )
        return PositionSizer.check_order_size(order_dollars, current_capital)
