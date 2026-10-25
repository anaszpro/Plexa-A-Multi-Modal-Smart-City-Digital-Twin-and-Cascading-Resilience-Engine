"""Past-only counts of eligible positive segment-hours for research features.

The caller supplies training-period positives and the frozen segment/grid bridge.
Counts are observations, not incident counts or calibrated probabilities. A delay
is an availability assumption; it does not establish historic publication times.
"""
import numpy as np
import pandas as pd


def _utc_times(values, name):
    times = pd.DatetimeIndex(pd.to_datetime(values, errors="raise"))
    if times.tz is None or times.hasnans:
        raise ValueError(f"{name} must contain resolved timezone-aware timestamps")
    times = times.tz_convert("UTC").as_unit("ns")
    if not times.equals(times.floor("h")):
        raise ValueError(f"{name} must be aligned to hours")
    return times


class HistoricalReportFeatures:
    """Count positives in [min(t-delay, train_end)-lookback, min(...))."""

    def __init__(self, events, *, training_start, training_end,
                 lookback_days=365, delay_days=7):
        if (not isinstance(lookback_days, int) or lookback_days < 1
                or not isinstance(delay_days, int) or delay_days < 1):
            raise ValueError("Lookback and delay must be positive integer days")
        self.training_start = _utc_times([training_start], "training_start")[0]
        self.training_end = _utc_times([training_end], "training_end")[0]
        if self.training_start >= self.training_end:
            raise ValueError("Training interval must be increasing")
        self.lookback_days = lookback_days
        self.delay_days = delay_days
        self.feature_names = [f"segment_positive_hours_{lookback_days}d",
                              f"cell_positive_hours_{lookback_days}d"]
        required = ["street_segment_id", "grid_id", "cell_id", "prediction_time_utc"]
        self.events = events[required].copy().reset_index(drop=True)
        self._validate_identity(self.events)
        times = _utc_times(self.events.prediction_time_utc, "history times")
        if ((times < self.training_start) | (times >= self.training_end)).any():
            raise ValueError("Historical events must be inside the training interval")
        self.events["prediction_time_utc"] = times
        if self.events.duplicated(["street_segment_id", "prediction_time_utc"]).any():
            raise ValueError("Duplicate positive segment-hour in history")
        bridge = self.events[["street_segment_id", "grid_id", "cell_id"]].drop_duplicates()
        if bridge.street_segment_id.duplicated().any():
            raise ValueError("One physical segment maps to multiple radar cells")
        self._bridge = bridge
        values = times.asi8
        self._segment_times = self._index(self.events, ["street_segment_id"], values)
        self._cell_times = self._index(self.events, ["grid_id", "cell_id"], values)

    @staticmethod
    def _validate_identity(frame):
        if frame[["street_segment_id", "grid_id", "cell_id"]].isna().any().any():
            raise ValueError("Missing street/grid/cell identity")
        for name in ["street_segment_id", "grid_id"]:
            if not frame[name].map(lambda value: isinstance(value, str) and bool(value)).all():
                raise ValueError(f"{name} must contain nonempty strings")
        cells = pd.to_numeric(frame.cell_id, errors="raise").to_numpy(dtype=float)
        if not (np.isfinite(cells).all() and (cells >= 0).all()
                and (cells == np.floor(cells)).all()):
            raise ValueError("Invalid cell IDs")

    @staticmethod
    def _index(frame, keys, times):
        # A scalar key for segment groups; a tuple for grid/cell groups.
        group_keys = keys[0] if len(keys) == 1 else keys
        return {key: np.sort(times[positions])
                for key, positions in frame.groupby(group_keys, sort=False).indices.items()}

    @staticmethod
    def _count(frame, keys, event_index, lower, upper):
        counts = np.zeros(len(frame), dtype=np.int64)
        group_keys = keys[0] if len(keys) == 1 else keys
        for key, positions in frame.groupby(group_keys, sort=False).indices.items():
            events = event_index.get(key)
            if events is not None:
                counts[positions] = (
                    np.searchsorted(events, upper[positions], side="left")
                    - np.searchsorted(events, lower[positions], side="left"))
        return counts

    def transform(self, rows):
        """Return two features plus audit metadata, preserving row order/index.

        `history_window_snapshot_span_days` describes calendar overlap with the
        training snapshot, not verified reporting/feature coverage or exposure.
        """
        self._validate_identity(rows)
        frame = rows.reset_index(drop=True)
        times = _utc_times(frame.prediction_time_utc, "prediction times")
        bridge_check = frame[["street_segment_id", "grid_id", "cell_id"]].merge(
            self._bridge, on="street_segment_id", how="inner", suffixes=("", "_history"))
        if ((bridge_check.grid_id != bridge_check.grid_id_history)
                | (bridge_check.cell_id != bridge_check.cell_id_history)).any():
            raise ValueError("Input grid/cell differs from the historical segment bridge")
        day_ns = pd.Timedelta(days=1).value
        upper = np.minimum(times.asi8 - self.delay_days * day_ns,
                           self.training_end.value)
        lower = upper - self.lookback_days * day_ns
        segment = self._count(frame, ["street_segment_id"], self._segment_times, lower, upper)
        cell = self._count(frame, ["grid_id", "cell_id"], self._cell_times, lower, upper)
        if (segment > cell).any():
            raise ValueError("Segment history cannot exceed its radar-cell history")
        return pd.DataFrame({
            self.feature_names[0]: segment,
            self.feature_names[1]: cell,
            "history_cutoff_utc": pd.to_datetime(upper, utc=True),
            "history_window_start_utc": pd.to_datetime(lower, utc=True),
            "history_window_snapshot_span_days": np.clip(
                (upper - np.maximum(lower, self.training_start.value)) / day_ns,
                0, self.lookback_days),
        }, index=rows.index)
