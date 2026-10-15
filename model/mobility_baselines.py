"""Small road-balanced profile baselines. All fitted values use training rows only."""

from dataclasses import dataclass
import pickle
from pathlib import Path

import numpy as np
import pandas as pd


QUANTILES = np.array([0.1, 0.5, 0.9])
QCOLS = ["q10", "q50", "q90"]


def road_weights(rows):
    return 1.0 / rows.groupby("sd", observed=True).sd.transform("size").to_numpy()


def weighted_quantiles(values, weights, probabilities=QUANTILES):
    values, weights = np.asarray(values, dtype=float), np.asarray(weights, dtype=float)
    if len(values) == 0 or not np.isfinite(values).all() or not np.isfinite(weights).all():
        raise ValueError("Quantile inputs must be finite and nonempty.")
    if np.any(weights < 0) or weights.sum() <= 0:
        raise ValueError("Quantile weights must be nonnegative with positive total.")
    keep = weights > 0
    values, weights = values[keep], weights[keep]
    order = np.argsort(values, kind="stable")
    values, weights = values[order], weights[order]
    cumulative = np.cumsum(weights, dtype=np.longdouble)
    # Treat round-off at a CDF boundary as an exact tie. Replicating a road's
    # observations must not change its quantiles solely through floating sums.
    thresholds = np.asarray(probabilities, dtype=np.longdouble) * cumulative[-1]
    positions = np.searchsorted(cumulative, thresholds - 1e-12 * cumulative[-1], side="left")
    return values[np.minimum(positions, len(values) - 1)]


def grouped_quantiles(rows, columns, values, weights):
    """Weighted inverse empirical CDF, with no interpolation across observations."""
    work = rows[columns].copy()
    work["_value"] = np.asarray(values)
    work["_weight"] = np.asarray(weights)
    records = []
    grouper = columns[0] if len(columns) == 1 else columns
    for key, group in work.groupby(grouper, observed=True, sort=True, dropna=False):
        key = key if isinstance(key, tuple) else (key,)
        records.append(dict(zip(columns, key)) | dict(zip(QCOLS,
            weighted_quantiles(group._value.to_numpy(), group._weight.to_numpy()))))
    return pd.DataFrame(records).set_index(columns)


def lookup(table, rows, columns):
    index = rows[columns[0]] if len(columns) == 1 else pd.MultiIndex.from_frame(rows[columns])
    return table.reindex(index).to_numpy(dtype=float)


def road_group(rows):
    borough = rows.borough.astype("string").fillna("unknown")
    lanes = rows.number_travel_lanes
    lane_group = np.select([lanes.eq(1), lanes.eq(2), lanes.eq(3), lanes.ge(4)],
                           ["1", "2", "3", "4+"], default="unknown")
    return borough + "|lanes=" + lane_group


@dataclass
class GlobalProfile:
    slots: pd.DataFrame
    hours: pd.DataFrame
    city: np.ndarray

    @classmethod
    def fit(cls, train):
        weights = road_weights(train)
        target = np.log1p(train.volume.to_numpy())
        return cls(grouped_quantiles(train, ["dow", "hour"], target, weights),
                   grouped_quantiles(train, ["hour"], target, weights),
                   weighted_quantiles(target, weights))

    def log_quantiles(self, rows):
        result = lookup(self.slots[QCOLS], rows, ["dow", "hour"])
        hourly = lookup(self.hours[QCOLS], rows, ["hour"])
        result = np.where(np.isfinite(result), result, hourly)
        return np.where(np.isfinite(result), result, self.city[None, :])

    def point_log(self, rows):
        return self.log_quantiles(rows)[:, 1]

    def predict(self, rows):
        return prediction_frame(self.log_quantiles(rows))

    def save(self, directory):
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=False)
        with (directory / "model.pkl").open("xb") as stream:
            pickle.dump(self, stream, protocol=pickle.HIGHEST_PROTOCOL)
        self.slots.reset_index().to_parquet(directory / "global_profiles.parquet", index=False)
        self.hours.reset_index().to_parquet(directory / "hour_fallbacks.parquet", index=False)
        return list(directory.iterdir())


@dataclass
class RoadScaledProfile:
    global_profile: GlobalProfile
    group_slots: pd.DataFrame
    levels: pd.DataFrame
    shapes: pd.DataFrame
    group_prior_roads: float = 20.0
    level_prior_days: float = 2.0
    shape_prior_days: float = 3.0
    residual_hours: pd.DataFrame | None = None
    residual_city: np.ndarray | None = None

    @classmethod
    def fit(cls, train, group_prior_roads=20.0, level_prior_days=2.0, shape_prior_days=3.0):
        if min(group_prior_roads, level_prior_days, shape_prior_days) <= 0:
            raise ValueError("Shrinkage priors must be positive.")
        global_profile = GlobalProfile.fit(train)
        work = train[["sd", "hour", "dow", "date"]].copy()
        work["road_group"] = road_group(train).to_numpy()
        target = np.log1p(train.volume.to_numpy())
        profile = grouped_quantiles(work, ["road_group", "dow", "hour"], target, road_weights(train))
        support = work.groupby(["road_group", "dow", "hour"], observed=True).sd.nunique()
        profile["n_roads"] = support
        profile["shrinkage"] = support / (support + group_prior_roads)
        cell_rows = profile.reset_index()
        city = global_profile.point_log(cell_rows)
        profile["log_profile"] = profile.shrinkage.to_numpy() * profile.q50.to_numpy() + (
            1 - profile.shrinkage.to_numpy()) * city
        model = cls(global_profile, profile, pd.DataFrame(), pd.DataFrame(),
                    group_prior_roads, level_prior_days, shape_prior_days)
        group_prediction = model.group_point_log(train)
        work["residual"] = target - group_prediction
        levels = work.groupby("sd", observed=True).agg(
            raw_offset=("residual", "median"), n_training_days=("date", "nunique"), n_rows=("date", "size"))
        levels["shrinkage"] = levels.n_training_days / (levels.n_training_days + level_prior_days)
        levels["offset"] = levels.raw_offset * levels.shrinkage
        work["shape_residual"] = work.residual.to_numpy() - work.sd.map(levels.offset).astype(float).to_numpy()
        shapes = work.groupby(["sd", "hour"], observed=True).agg(
            raw_shape=("shape_residual", "median"), n_training_days=("date", "nunique"))
        shapes["shrinkage"] = shapes.n_training_days / (shapes.n_training_days + shape_prior_days)
        shapes["shape"] = shapes.raw_shape * shapes.shrinkage
        # Centre the observed hourly shape so the road level stays a separate term.
        shapes["shape"] -= shapes.groupby(level="sd", observed=True)["shape"].transform("median")
        model.levels, model.shapes = levels, shapes
        return model

    def group_point_log(self, rows):
        work = rows[["dow", "hour"]].copy()
        work["road_group"] = road_group(rows).to_numpy()
        values = lookup(self.group_slots[["log_profile"]], work, ["road_group", "dow", "hour"])[:, 0]
        return np.where(np.isfinite(values), values, self.global_profile.point_log(rows))

    def point_log(self, rows):
        group = self.group_point_log(rows)
        level = self.levels.offset.reindex(rows.sd).fillna(0).to_numpy()
        shape = lookup(self.shapes[["shape"]], rows, ["sd", "hour"])[:, 0]
        return group + level + np.nan_to_num(shape, nan=0.0)

    def calibrate(self, train, oof_point_log):
        """Empirical residual quantiles from held-out training folds, never test."""
        oof_point_log = np.asarray(oof_point_log, dtype=float)
        if len(oof_point_log) != len(train) or not np.isfinite(oof_point_log).all():
            raise ValueError("Every training row needs a finite out-of-fold prediction.")
        residual = np.log1p(train.volume.to_numpy()) - oof_point_log
        weights = road_weights(train)
        self.residual_city = weighted_quantiles(residual, weights)
        hourly = grouped_quantiles(train, ["hour"], residual, weights)
        support = train.groupby("hour", observed=True).sd.nunique()
        hourly["n_roads"] = support
        hourly["shrinkage"] = support / (support + self.group_prior_roads)
        for i, column in enumerate(QCOLS):
            hourly[column] = hourly.shrinkage * hourly[column] + (1 - hourly.shrinkage) * self.residual_city[i]
        self.residual_hours = hourly

    def predict(self, rows):
        if self.residual_hours is None or self.residual_city is None:
            raise RuntimeError("Calibrate intervals using training OOF predictions first.")
        residual = lookup(self.residual_hours[QCOLS], rows, ["hour"])
        residual = np.where(np.isfinite(residual), residual, self.residual_city[None, :])
        return prediction_frame(self.point_log(rows)[:, None] + residual)

    def save(self, directory):
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=False)
        with (directory / "model.pkl").open("xb") as stream:
            pickle.dump(self, stream, protocol=pickle.HIGHEST_PROTOCOL)
        self.group_slots.reset_index().to_parquet(directory / "group_profiles.parquet", index=False)
        self.levels.reset_index().to_parquet(directory / "road_levels.parquet", index=False)
        self.shapes.reset_index().to_parquet(directory / "road_hour_shapes.parquet", index=False)
        self.residual_hours.reset_index().to_parquet(directory / "interval_calibration.parquet", index=False)
        return list(directory.iterdir())


def prediction_frame(log_predictions):
    values = np.expm1(np.maximum(np.asarray(log_predictions), 0))
    if not np.isfinite(values).all() or np.any(np.diff(values, axis=1) < -1e-10):
        raise RuntimeError("Predictions must be finite, nonnegative and ordered.")
    return pd.DataFrame(values, columns=["p10", "p50", "p90"])


def calibration_folds(train, scenario, n_folds=3, seed=42):
    """Whole segments for cold start; balanced segment/date groups for known roads."""
    rng = np.random.default_rng(seed)
    if scenario == "unseen_roads":
        segments = train[["segment_id", "borough"]].drop_duplicates("segment_id").sort_values("segment_id")
        segments["stratum"] = segments.borough.astype("string").fillna("unknown")
        assignment = {}
        for _, group in segments.groupby("stratum", sort=True):
            ids = group.segment_id.to_numpy()
            for i, segment in enumerate(rng.permutation(ids)):
                assignment[int(segment)] = i % n_folds
        folds = train.segment_id.map(assignment).to_numpy(dtype=np.int8)
    else:
        unique = train[["segment_id", "sd", "date"]].drop_duplicates().sort_values(["segment_id", "sd", "date"])
        assignment = {}
        for segment, group in unique.groupby("segment_id", sort=True):
            dates = sorted(group.date.unique())
            date_index = {day: i for i, day in enumerate(dates)}
            direction_indices = [np.array([date_index[day] for day in part.date])
                                 for _, part in group.groupby("sd", observed=True)]
            for attempt in range(100):
                candidate = rng.permutation(np.arange(len(dates)) % n_folds)
                if all(np.unique(candidate[index]).size >= 2 for index in direction_indices):
                    break
            else:
                raise RuntimeError(f"Could not preserve history in training folds for segment {segment}.")
            assignment.update({(int(segment), day): int(candidate[i]) for i, day in enumerate(dates)})
        folds = np.array([assignment[(int(segment), day)] for segment, day in zip(train.segment_id, train.date)], dtype=np.int8)
    for fold in range(n_folds):
        fit = train.loc[folds != fold]
        held = train.loc[folds == fold]
        if len(fit) == 0 or len(held) == 0:
            raise RuntimeError("Empty calibration fold.")
        if scenario == "unseen_roads":
            if not set(fit.segment_id).isdisjoint(set(held.segment_id)):
                raise RuntimeError("Segment leaked into cold-start OOF fit.")
        elif not set(held.sd).issubset(set(fit.sd)):
            raise RuntimeError("Known-road calibration unexpectedly lost road history.")
    return folds
