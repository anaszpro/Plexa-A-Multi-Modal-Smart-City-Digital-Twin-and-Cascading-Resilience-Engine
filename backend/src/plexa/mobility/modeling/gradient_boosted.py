"""Fixed LightGBM log-quantile benchmark with training-only road-history features."""

from dataclasses import dataclass
import json
from pathlib import Path
import pickle

import lightgbm as lgb
import numpy as np
import pandas as pd

from modeling.mobility_baselines import RoadScaledProfile, road_weights


NUMERIC_FEATURES = [
    "hour", "dow", "month", "year", "is_weekend", "is_holiday", "holiday_adjacent", "covid",
    "posted_speed", "number_travel_lanes", "number_park_lanes", "number_total_lanes",
    "streetwidth_min", "streetwidth_max", "shape_length", "lon", "lat",
]
CATEGORICAL_FEATURES = ["direction", "borough", "trafdir", "truck_route_type", "snow_priority", "bikelane", "source"]
CYCLIC_FEATURES = ["hour_sin", "hour_cos", "dow_sin", "dow_cos", "month_sin", "month_cos"]
HISTORY_FEATURES = ["history_profile_log", "history_days", "has_history"]
FEATURE_COLUMNS = NUMERIC_FEATURES + CATEGORICAL_FEATURES + CYCLIC_FEATURES + HISTORY_FEATURES
QUANTILES = [0.1, 0.5, 0.9]


def require(condition, message):
    if not condition:
        raise ValueError(message)


def make_training_history(train, oof, scenario):
    """Join saved out-of-fold base predictions and compute fold-fit history counts.

    observed_volume, when present, is checked for identity alignment only.
    It and log_residual are never returned as predictors.
    """
    require(train.index.is_unique, "Training model_row offsets are not unique.")
    require(not oof.model_row.duplicated().any(), "OOF model_row offsets are duplicated.")
    require(set(oof.model_row) == set(train.index), "OOF and training row identities differ.")
    aligned = oof.set_index("model_row").reindex(train.index)
    require(set(aligned.fold.unique()) == {0, 1, 2}, "Expected three saved OOF folds.")
    require(np.isfinite(aligned.base_log_prediction).all(), "Nonfinite OOF history predictions.")
    if "observed_volume" in aligned:
        require(np.array_equal(aligned.observed_volume.to_numpy(), train.volume.to_numpy()),
                "OOF observations do not match the frozen training rows.")
    groups = train[["segment_id", "sd", "date"]].copy()
    groups["fold"] = aligned.fold.to_numpy()
    require(groups.groupby(["segment_id", "date"], observed=True).fold.nunique().eq(1).all(),
            "A segment/date spans OOF folds.")
    if scenario == "unseen_roads":
        require(groups.groupby("segment_id").fold.nunique().eq(1).all(), "Cold-start segment leakage.")
    result = pd.DataFrame(index=train.index)
    result["history_profile_log"] = aligned.base_log_prediction.to_numpy()
    result["history_days"] = 0
    for fold in range(3):
        held = aligned.fold.eq(fold).to_numpy()
        fit_days = train.loc[~held].groupby("sd", observed=True).date.nunique()
        result.loc[held, "history_days"] = train.loc[held].sd.astype("string").map(fit_days).fillna(0).to_numpy()
    result["has_history"] = result.history_days.gt(0).astype("int8")
    result["fold"] = aligned.fold.to_numpy(dtype="int8")
    if scenario == "unseen_roads":
        require(result.history_days.eq(0).all(), "Cold-start OOF unexpectedly has road history.")
    else:
        require(result.history_days.gt(0).all(), "Known-road OOF lost road history.")
    return result


@dataclass
class FeatureEncoder:
    categories: dict

    @classmethod
    def fit(cls, rows):
        missing = set(NUMERIC_FEATURES + CATEGORICAL_FEATURES) - set(rows.columns)
        require(not missing, f"Missing feature inputs: {sorted(missing)}")
        categories = {}
        for col in CATEGORICAL_FEATURES:
            categories[col] = sorted(set(rows[col].astype("string").fillna("__MISSING__")) | {"__MISSING__"})
        return cls(categories)

    @property
    def feature_columns(self):
        return FEATURE_COLUMNS.copy()

    def transform(self, rows, history_log, history_days):
        rows = rows.reset_index(drop=True)
        history_log, history_days = np.asarray(history_log, dtype=float), np.asarray(history_days, dtype=float)
        require(len(history_log) == len(rows) == len(history_days), "History features do not align with rows.")
        require(np.isfinite(history_log).all() and np.isfinite(history_days).all() and (history_days >= 0).all(),
                "Invalid training-history features.")
        result = pd.DataFrame(index=rows.index)
        for col in NUMERIC_FEATURES:
            result[col] = pd.to_numeric(rows[col], errors="raise").astype("float64")
        require(not np.isinf(result.to_numpy()).any(), "Infinite numeric feature.")
        for col in CATEGORICAL_FEATURES:
            result[col] = pd.Categorical(rows[col].astype("string").fillna("__MISSING__"), categories=self.categories[col])
        for name, period, offset in [("hour", 24, 0), ("dow", 7, 0), ("month", 12, 1)]:
            angle = 2 * np.pi * (rows[name].to_numpy() - offset) / period
            result[name + "_sin"] = np.sin(angle)
            result[name + "_cos"] = np.cos(angle)
        result["history_profile_log"] = history_log
        result["history_days"] = history_days
        result["has_history"] = (history_days > 0).astype("int8")
        return result[FEATURE_COLUMNS]


@dataclass
class BoostedTrafficModel:
    encoder: FeatureEncoder
    quantile_models: dict
    history_model: RoadScaledProfile
    training_days: pd.Series
    parameters: dict

    @classmethod
    def fit(cls, train, history_log, history_days, history_model, seed=42, n_estimators=300):
        require(len(train) > 0 and train.volume.ge(0).all() and np.isfinite(train.volume).all(), "Invalid training targets.")
        encoder = FeatureEncoder.fit(train)
        features = encoder.transform(train, history_log, history_days)
        target = np.log1p(train.volume.to_numpy())
        sample_weight = road_weights(train)
        sample_weight /= sample_weight.mean()  # Preserve equal-road ratios; stable penalty scale.
        parameters = dict(
            objective="quantile", n_estimators=n_estimators, learning_rate=0.05,
            num_leaves=31, max_depth=-1, min_child_samples=100, reg_lambda=1.0,
            subsample=1.0, colsample_bytree=1.0, max_bin=127,
            deterministic=True, force_col_wise=True, random_state=seed, n_jobs=4, verbosity=-1,
        )
        models = {}
        for alpha in QUANTILES:
            estimator = lgb.LGBMRegressor(alpha=alpha, **parameters)
            estimator.fit(features, target, sample_weight=sample_weight,
                          categorical_feature=CATEGORICAL_FEATURES)
            models[alpha] = estimator
            print(f"    LightGBM q{int(alpha * 100):02d}: {estimator.n_estimators_} trees fitted", flush=True)
        days = train.groupby("sd", observed=True).date.nunique()
        return cls(encoder, models, history_model, days, parameters)

    def features_for_prediction(self, rows):
        history = self.history_model.point_log(rows)
        days = rows.sd.astype("string").map(self.training_days).fillna(0).to_numpy()
        return self.encoder.transform(rows, history, days)

    def raw_log_predictions(self, rows):
        features = self.features_for_prediction(rows)
        return np.column_stack([self.quantile_models[alpha].predict(features) for alpha in QUANTILES])

    def predict(self, rows):
        # A fixed monotone rearrangement resolves independently fitted quantile crossings.
        logs = np.sort(self.raw_log_predictions(rows), axis=1)
        predictions = np.expm1(np.maximum(logs, 0))
        require(np.isfinite(predictions).all(), "Nonfinite boosted prediction.")
        return pd.DataFrame(predictions, columns=["p10", "p50", "p90"])

    def feature_importance(self):
        booster = self.quantile_models[0.5].booster_
        return pd.DataFrame({"feature": booster.feature_name(),
                             "gain": booster.feature_importance(importance_type="gain"),
                             "split_count": booster.feature_importance(importance_type="split")})

    def save(self, directory):
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=False)
        with (directory / "model.pkl").open("xb") as stream:
            pickle.dump(self, stream, protocol=pickle.HIGHEST_PROTOCOL)
        for alpha, estimator in self.quantile_models.items():
            estimator.booster_.save_model(str(directory / f"quantile_{int(alpha * 100):02d}.txt"))
        schema = {"feature_columns": FEATURE_COLUMNS, "numeric_features": NUMERIC_FEATURES,
                  "categorical_features": CATEGORICAL_FEATURES, "categories": self.encoder.categories,
                  "cyclic_features": CYCLIC_FEATURES, "history_features": HISTORY_FEATURES,
                  "target_transform": "log1p(volume)", "output_unit": "vehicles/hour",
                  "quantile_rearrangement": "Sort the three predicted log-quantiles, then clip log>=0 and expm1.",
                  "missing_values": "Preserved as NaN; new categorical values map to missing.",
                  "provenance": "OOF training history; full-training history for inference. "
                                "No raw road IDs, target values, residuals or full-snapshot coverage as tree features."}
        (directory / "feature_schema.json").write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
        return list(directory.iterdir())
