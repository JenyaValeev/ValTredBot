# utils.py
import numpy as np
from typing import Iterable
import math

def calculate_drawdown(curve: Iterable[float]) -> float:
    peak = -math.inf
    max_dd = 0.0
    for v in curve:
        if v > peak:
            peak = v
        if peak > 0:
            dd = (peak - v) / peak
            if dd > max_dd:
                max_dd = dd
    return max_dd

def calculate_sharpe(curve: np.ndarray, risk_free_rate=0.0) -> float:
    if len(curve) < 2:
        return 0.0
    returns = np.diff(curve) / curve[:-1]
    excess = returns - risk_free_rate/365
    std = np.std(excess)
    return float((np.sqrt(365) * np.mean(excess) / std)) if std > 0 else 0.0

def cagr(initial: float, final: float, days: float) -> float:
    if initial <= 0 or days <= 0:
        return 0.0
    return (final / initial) ** (365.0/days) - 1.0
