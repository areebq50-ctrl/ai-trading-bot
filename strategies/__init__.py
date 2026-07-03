from .trend_following import TrendFollowingStrategy
from .mean_reversion import MeanReversionStrategy

REGISTRY = {
    "trend_following": TrendFollowingStrategy,
    "mean_reversion": MeanReversionStrategy,
}


def get_strategy(name: str, **kwargs):
    if name not in REGISTRY:
        raise ValueError(f"Unknown strategy '{name}'. Choose from: {list(REGISTRY)}")
    return REGISTRY[name](**kwargs)
