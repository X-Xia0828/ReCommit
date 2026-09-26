"""Randomness."""

from __future__ import annotations


def seed_torch(seed: int) -> dict[str, bool]:
    """Seed Python / NumPy / Torch for one generation draw."""
    import random

    random.seed(seed)
    status = {"python": True, "numpy": False, "torch": False}
    try:
        import numpy as np

        np.random.seed(seed)
        status["numpy"] = True
    except ImportError:
        pass
    try:
        import torch

        torch.manual_seed(seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(seed)
        status["torch"] = True
    except ImportError:
        pass
    return status
