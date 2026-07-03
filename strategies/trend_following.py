"""
Trend-following strategy: moving average crossover.
Signal = +1 (long) when fast MA > slow MA, 0 (flat) otherwise.
"""
import pandas as pd


class TrendFollowingStrategy:
    def __init__(self, fast_window: int = 10, slow_window: int = 50, **_):
        self.fast_window = fast_window
        self.slow_window = slow_window
        self.name = "trend_following"

    def compute_signals(self, prices: pd.Series) -> pd.DataFrame:
        """
        Returns a DataFrame with columns: fast_ma, slow_ma, signal (0 or 1).
        Requires at least slow_window data points before emitting a non-NaN signal.
        """
        df = pd.DataFrame({"price": prices})
        df["fast_ma"] = df["price"].rolling(self.fast_window).mean()
        df["slow_ma"] = df["price"].rolling(self.slow_window).mean()
        # 1 = hold long, 0 = flat (no shorting — the account is long-only)
        df["signal"] = (df["fast_ma"] > df["slow_ma"]).astype(int)
        df["signal"] = df["signal"].where(df["slow_ma"].notna(), other=0)
        df["description"] = df.apply(self._describe, axis=1)
        return df

    def _describe(self, row) -> str:
        if pd.isna(row["slow_ma"]):
            return "warming up — insufficient history"
        direction = "LONG" if row["signal"] == 1 else "FLAT"
        return (
            f"{direction}: fast_ma={row['fast_ma']:.2f} "
            f"{'>' if row['signal'] else '<='} slow_ma={row['slow_ma']:.2f}"
        )
