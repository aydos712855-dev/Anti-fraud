"""
General anomaly detector for latency arbitrage and lucky-strategy profiles.
Latency arbitrage: very short profitable trades, opened at price divergence moments.
"""
from __future__ import annotations

import logging
from datetime import timedelta

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest

logger = logging.getLogger(__name__)


def compute_latency_arbitrage_features(trades_df: pd.DataFrame) -> pd.Series:
    """
    Features for detecting latency arbitrage / quote exploitation.
    Requires: open_time, close_time (or duration_ms), profit, symbol
    """
    if trades_df.empty:
        return pd.Series(dtype=float)

    df = trades_df.copy()
    df["duration_ms"] = df.get("duration_ms", pd.Series([np.nan] * len(df)))

    if df["duration_ms"].isna().all() and "close_time" in df.columns:
        df["open_time"] = pd.to_datetime(df["open_time"], utc=True)
        df["close_time"] = pd.to_datetime(df["close_time"], utc=True)
        df["duration_ms"] = (df["close_time"] - df["open_time"]).dt.total_seconds() * 1000

    ultra_short = df[df["duration_ms"] < 5000]  # < 5 seconds
    short_profitable = ultra_short[ultra_short.get("profit", pd.Series(0)) > 0] if "profit" in df else ultra_short

    features: dict = {}
    features["frac_ultra_short"] = len(ultra_short) / max(len(df), 1)
    features["frac_short_profitable"] = len(short_profitable) / max(len(df), 1)

    if "profit" in df.columns:
        profits = df["profit"].fillna(0).astype(float)
        features["overall_win_rate"] = float(np.mean(profits > 0))
        features["short_win_rate"] = (
            float(np.mean(ultra_short["profit"].fillna(0) > 0))
            if len(ultra_short) > 0 else 0.0
        )
    else:
        features["overall_win_rate"] = 0.5
        features["short_win_rate"] = 0.5

    # Profit concentration in short trades
    if "profit" in df.columns and len(ultra_short) > 0:
        total_profit = df["profit"].fillna(0).astype(float).sum()
        short_profit = ultra_short["profit"].fillna(0).astype(float).sum()
        features["short_profit_share"] = (
            float(short_profit / (abs(total_profit) + 1e-9))
        )
    else:
        features["short_profit_share"] = 0.0

    return pd.Series(features)


def score_latency_arbitrage(trades_df: pd.DataFrame) -> dict:
    """
    Returns {"flagged": bool, "score": float, "explanation": str}
    """
    features = compute_latency_arbitrage_features(trades_df)
    if features.empty:
        return {"flagged": False, "score": 0.0, "explanation": "Недостаточно данных"}

    score = 0.0
    reasons = []

    if features.get("frac_ultra_short", 0) > 0.3:
        score += 0.35
        reasons.append(f"{features['frac_ultra_short']*100:.0f}% сделок длительностью <5с")

    if features.get("short_win_rate", 0) > 0.75:
        score += 0.3
        reasons.append(f"Win rate на коротких сделках: {features['short_win_rate']*100:.0f}%")

    if features.get("short_profit_share", 0) > 0.6:
        score += 0.25
        reasons.append(f"{features['short_profit_share']*100:.0f}% прибыли от сверхкоротких сделок")

    if features.get("overall_win_rate", 0) > 0.8:
        score += 0.1
        reasons.append(f"Аномально высокий общий win rate: {features['overall_win_rate']*100:.0f}%")

    return {
        "flagged": score > 0.5,
        "score": min(round(score, 3), 1.0),
        "features": features.to_dict(),
        "explanation": "; ".join(reasons) if reasons else "Признаков арбитража не обнаружено",
    }
