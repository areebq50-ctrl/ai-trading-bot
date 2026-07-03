"""
Mean-reversion strategy: z-score of price vs rolling mean.
Enter long when z < entry_z, exit when z >= exit_z.
"""
import pandas as pd


class MeanReversionStrategy:
    def __init__(
        self,
        window: int = 20,
        entry_z: float = -1.2,
        exit_z: float = 0.0,
        **_,
    ):
        self.window = window
        self.entry_z = entry_z
        self.exit_z = exit_z
        self.name = "mean_reversion"

    def compute_signals(self, prices: pd.Series) -> pd.DataFrame:
        """
        Returns a DataFrame with columns: rolling_mean, rolling_std, z_score, signal.
        signal = 1 means hold the position; 0 means flat.
        Entry and exit are modelled as a state machine: once entered on z < entry_z,
        stay long until z >= exit_z.
        """
        df = pd.DataFrame({"price": prices})
        df["rolling_mean"] = df["price"].rolling(self.window).mean()
        df["rolling_std"] = df["price"].rolling(self.window).std()
        df["z_score"] = (df["price"] - df["rolling_mean"]) / df["rolling_std"]

        # State-machine signal: enter on strong dip, exit on mean reversion
        signal = []
        in_position = False
        for z in df["z_score"]:
            if pd.isna(z):
                signal.append(0)
                continue
            if not in_position and z <= self.entry_z:
                in_position = True
            elif in_position and z >= self.exit_z:
                in_position = False
            signal.append(1 if in_position else 0)

        df["signal"] = signal
        df["description"] = df.apply(self._describe, axis=1)
        return df

    def _describe(self, row) -> str:
        if pd.isna(row["z_score"]):
            return "warming up — insufficient history"
        direction = "LONG" if row["signal"] == 1 else "FLAT"
        return (
            f"{direction}: z={row['z_score']:.3f} "
            f"(mean={row['rolling_mean']:.2f}, "
            f"entry_z={self.entry_z}, exit_z={self.exit_z})"
        )
