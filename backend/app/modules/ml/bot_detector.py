"""
ML bot detector.
Features: inter-trade interval statistics, reaction speed, 24h activity patterns,
volume regularity, order modification counts.
Model: IsolationForest for unsupervised anomaly detection + optional supervised
RandomForest when labeled data is available.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest, RandomForestClassifier
from sklearn.preprocessing import StandardScaler

logger = logging.getLogger(__name__)

MODEL_PATH = Path(os.getenv("MODELS_STORAGE_PATH", "models_storage")) / "bot_detector.joblib"
SCALER_PATH = Path(os.getenv("MODELS_STORAGE_PATH", "models_storage")) / "bot_scaler.joblib"


def _extract_features(trades_df: pd.DataFrame) -> pd.Series:
    """
    Given a DataFrame of trades for one account, extract bot-detection features.
    Required columns: open_time, volume, duration_ms, profit
    """
    if len(trades_df) < 3:
        return pd.Series(dtype=float)

    df = trades_df.copy()
    df["open_time"] = pd.to_datetime(df["open_time"], utc=True)
    df = df.sort_values("open_time")

    # Inter-trade intervals in ms
    intervals = df["open_time"].diff().dt.total_seconds().dropna() * 1000
    iv = intervals.values

    features: dict[str, float] = {}

    # Interval regularity
    features["iv_mean_ms"] = float(np.mean(iv))
    features["iv_std_ms"] = float(np.std(iv))
    features["iv_cv"] = float(np.std(iv) / (np.mean(iv) + 1e-9))  # coefficient of variation
    features["iv_min_ms"] = float(np.min(iv))
    features["iv_p10_ms"] = float(np.percentile(iv, 10))

    # Fraction of trades with sub-human reaction (< 500ms)
    features["frac_fast_trades"] = float(np.mean(iv < 500))

    # Volume regularity
    vol = df["volume"].astype(float).values
    features["vol_cv"] = float(np.std(vol) / (np.mean(vol) + 1e-9))
    features["frac_round_volume"] = float(np.mean(vol % 0.01 < 1e-6))

    # 24h activity spread: fraction of hours with at least one trade
    df["hour"] = df["open_time"].dt.hour
    active_hours = df["hour"].nunique()
    features["active_hours_frac"] = active_hours / 24.0

    # Night trading (00-06 UTC)
    features["frac_night_trades"] = float(np.mean(df["hour"].between(0, 5)))

    # Win rate (suspicious if very high)
    if "profit" in df.columns:
        profits = df["profit"].fillna(0).astype(float)
        features["win_rate"] = float(np.mean(profits > 0))
    else:
        features["win_rate"] = 0.5

    # Average trade duration
    if "duration_ms" in df.columns:
        dur = df["duration_ms"].fillna(0).astype(float)
        features["avg_duration_ms"] = float(dur.mean())
    else:
        features["avg_duration_ms"] = 0.0

    return pd.Series(features)


class BotDetector:
    def __init__(self):
        self.model: IsolationForest | RandomForestClassifier | None = None
        self.scaler: StandardScaler | None = None
        self._load_model()

    def _load_model(self):
        if MODEL_PATH.exists() and SCALER_PATH.exists():
            try:
                self.model = joblib.load(MODEL_PATH)
                self.scaler = joblib.load(SCALER_PATH)
                logger.info("Bot detector model loaded from %s", MODEL_PATH)
            except Exception:
                logger.warning("Failed to load bot detector model, will train on first call")

    def predict(self, trades_df: pd.DataFrame) -> dict:
        """
        Returns {"is_bot": bool, "score": float, "features": dict, "explanation": str}
        score is in [0, 1] where 1 = most suspicious.
        """
        features = _extract_features(trades_df)
        if features.empty:
            return {"is_bot": False, "score": 0.0, "features": {}, "explanation": "Insufficient data"}

        X = features.values.reshape(1, -1)

        if self.model is None:
            # Heuristic fallback if no model trained yet
            return self._heuristic_predict(features)

        if self.scaler:
            X = self.scaler.transform(X)

        if isinstance(self.model, IsolationForest):
            raw = self.model.score_samples(X)[0]
            # IsolationForest score: more negative = more anomalous
            # Map to [0,1]: -0.5 → 1.0, 0.0 → 0.0
            score = float(np.clip(-raw / 0.5, 0, 1))
        else:
            prob = self.model.predict_proba(X)[0]
            score = float(prob[1]) if prob.shape[0] > 1 else float(prob[0])

        is_bot = score > 0.65
        explanation = self._explain(features, score)

        return {
            "is_bot": is_bot,
            "score": round(score, 3),
            "features": features.to_dict(),
            "explanation": explanation,
        }

    def _heuristic_predict(self, features: pd.Series) -> dict:
        """Rule-based fallback: flag clear bot signatures."""
        score = 0.0
        reasons = []

        if features.get("iv_cv", 1.0) < 0.1:
            score += 0.4
            reasons.append("очень равномерные интервалы между ордерами")

        if features.get("iv_min_ms", 9999) < 100:
            score += 0.3
            reasons.append("нечеловеческая скорость реакции (<100 мс)")

        if features.get("active_hours_frac", 0) > 0.85:
            score += 0.2
            reasons.append("торговля почти круглосуточно")

        if features.get("vol_cv", 1.0) < 0.05:
            score += 0.1
            reasons.append("одинаковые объёмы ордеров")

        return {
            "is_bot": score > 0.5,
            "score": min(score, 1.0),
            "features": features.to_dict(),
            "explanation": "; ".join(reasons) if reasons else "Признаков бота не обнаружено",
        }

    def _explain(self, features: pd.Series, score: float) -> str:
        parts = []
        if features.get("iv_cv", 1.0) < 0.15:
            parts.append("крайне равномерные интервалы между ордерами")
        if features.get("iv_min_ms", 9999) < 200:
            parts.append(f"минимальный интервал {features['iv_min_ms']:.0f} мс")
        if features.get("active_hours_frac", 0) > 0.8:
            parts.append(f"активность в {features['active_hours_frac']*24:.0f}/24 часах суток")
        if features.get("frac_fast_trades", 0) > 0.5:
            parts.append(f"{features['frac_fast_trades']*100:.0f}% сделок с реакцией <500мс")
        return "; ".join(parts) if parts else f"Аномальный паттерн (скор {score:.2f})"

    def train(self, trades_by_account: dict[int, pd.DataFrame], labels: dict[int, int] | None = None):
        """
        Train or retrain the model.
        trades_by_account: {account_id: trades_df}
        labels: optional {account_id: 1 (bot) | 0 (human)} for supervised training
        """
        rows = []
        ids = []
        for acc_id, df in trades_by_account.items():
            features = _extract_features(df)
            if not features.empty:
                rows.append(features)
                ids.append(acc_id)

        if not rows:
            logger.warning("No valid feature rows for bot detector training")
            return

        X = pd.DataFrame(rows).fillna(0)
        self.scaler = StandardScaler()
        X_scaled = self.scaler.fit_transform(X)

        if labels and len({labels.get(i) for i in ids if i in labels} - {None}) > 1:
            y = [labels.get(i, 0) for i in ids]
            self.model = RandomForestClassifier(n_estimators=200, class_weight="balanced", random_state=42)
            self.model.fit(X_scaled, y)
            logger.info("Bot detector: trained supervised RandomForest on %d samples", len(rows))
        else:
            self.model = IsolationForest(contamination=0.05, n_estimators=200, random_state=42)
            self.model.fit(X_scaled)
            logger.info("Bot detector: trained IsolationForest on %d samples", len(rows))

        MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(self.model, MODEL_PATH)
        joblib.dump(self.scaler, SCALER_PATH)
        logger.info("Bot detector model saved")
