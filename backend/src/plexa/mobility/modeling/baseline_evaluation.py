"""Leakage-free, equal-road evaluation for Plexa mobility baselines.

Predictions and observation rows are matched by position, not pandas index labels.
All traffic quantities (including quantiles) are in vehicles per hour. Overall
weights give every segment/direction (``sd``) the same influence; subgroup weights
are recomputed within each subgroup, rather than inherited from the whole test
partition. Nothing in this module fits or calibrates a model.
"""

from pathlib import Path

import numpy as np
import pandas as pd


METRIC_COLUMNS = [
    "mae", "rmse", "log1p_mae", "log1p_rmse", "coverage_80",
    "mean_interval_width", "pinball_10", "pinball_50", "pinball_90",
    "interval_score_80", "weighted_interval_score",
]
_AVERAGED_COLUMNS = [
    "mae", "mse", "log1p_mae", "log1p_mse", "coverage_80",
    "mean_interval_width", "pinball_10", "pinball_50", "pinball_90",
    "interval_score_80", "weighted_interval_score",
]
WEIGHTING_NOTE = (
    "Every road/direction has equal weight. Within each subgroup this weighting "
    "is recomputed using only that subgroup's rows."
)


def _validate_rows(rows):
    needed = {"volume", "sd", "segment_id"}
    if not needed.issubset(rows.columns):
        raise ValueError(f"Observation rows are missing: {sorted(needed - set(rows.columns))}")
    if len(rows) == 0:
        raise ValueError("Cannot evaluate an empty test partition.")
    if rows[["sd", "segment_id"]].isna().any().any():
        raise ValueError("Road and segment identities must not be missing.")
    y = rows["volume"].to_numpy(dtype=float)
    if not np.isfinite(y).all() or (y < 0).any():
        raise ValueError("Observed traffic volumes must be finite and nonnegative.")
    return y


def _prediction_array(predictions, n_rows):
    if not isinstance(predictions, pd.DataFrame):
        raise TypeError("Predictions must be a DataFrame with p10, p50 and p90 columns.")
    if len(predictions) != n_rows:
        raise ValueError("Observation and prediction row counts differ.")
    quantiles = predictions[["p10", "p50", "p90"]].to_numpy(dtype=float)
    if not np.isfinite(quantiles).all() or (quantiles < 0).any():
        raise ValueError("Predicted quantiles must be finite and nonnegative.")
    if (np.diff(quantiles, axis=1) < -1e-10).any():
        raise ValueError("Prediction quantiles cross: expected p10 <= p50 <= p90.")
    return quantiles


def equal_road_weights(rows):
    """Return positional weights with total one per sd in these exact rows."""
    if "sd" not in rows or rows["sd"].isna().any():
        raise ValueError("A nonmissing sd identifier is required for every row.")
    counts = rows.groupby("sd", observed=True)["sd"].transform("size")
    return 1.0 / counts.to_numpy(dtype=float)


def _weights(rows, weights):
    weight = equal_road_weights(rows) if weights is None else np.asarray(weights, dtype=float)
    if weight.ndim != 1 or len(weight) != len(rows):
        raise ValueError("Weights must have one value per observation, in the same order.")
    if not np.isfinite(weight).all() or (weight < 0).any() or weight.sum() <= 0:
        raise ValueError("Evaluation weights must be finite, nonnegative and nonzero.")
    totals = pd.Series(weight).groupby(rows["sd"].reset_index(drop=True), observed=True).sum()
    if not np.allclose(totals.to_numpy(), 1.0, atol=1e-10, rtol=1e-10):
        raise ValueError("Evaluation weights must sum to one per road/direction in this partition.")
    return weight


def _row_metrics(rows, predictions):
    y = _validate_rows(rows)
    q = _prediction_array(predictions, len(rows))
    residual = y - q[:, 1]
    log_residual = np.log1p(y) - np.log1p(q[:, 1])
    errors = y[:, None] - q
    pinball = np.maximum(errors * np.array([0.1, 0.5, 0.9]),
                         errors * np.array([-0.9, -0.5, -0.1]))
    width = q[:, 2] - q[:, 0]
    interval_score = width + 10.0 * (np.maximum(q[:, 0] - y, 0) +
                                    np.maximum(y - q[:, 2], 0))
    return pd.DataFrame({
        "mae": np.abs(residual), "mse": residual ** 2,
        "log1p_mae": np.abs(log_residual), "log1p_mse": log_residual ** 2,
        "coverage_80": ((y >= q[:, 0]) & (y <= q[:, 2])).astype(float),
        "mean_interval_width": width,
        "pinball_10": pinball[:, 0], "pinball_50": pinball[:, 1],
        "pinball_90": pinball[:, 2], "interval_score_80": interval_score,
        # Standard WIS for K=1: (0.5 * |y-median| + alpha/2 * IS) / (K+0.5).
        "weighted_interval_score": (0.5 * np.abs(residual) + 0.1 * interval_score) / 1.5,
    })


def _finish_metrics(averages):
    result = dict(averages)
    result["rmse"] = float(np.sqrt(result.pop("mse")))
    result["log1p_rmse"] = float(np.sqrt(result.pop("log1p_mse")))
    return {name: float(result[name]) for name in METRIC_COLUMNS}


def evaluate_predictions(rows, predictions, weights=None):
    """Evaluate test rows; supplied scenario weights must sum to one per sd.

    ``coverage_80`` is a proportion; interval endpoints count as covered. RMSE is
    the square root of the equal-road mean squared error, not the average of
    individual roads' RMSE values. WIS uses the median and the central 80% interval.
    """
    values = _row_metrics(rows, predictions)
    weight = _weights(rows, weights)
    averages = np.average(values[_AVERAGED_COLUMNS].to_numpy(), axis=0, weights=weight)
    result = _finish_metrics(dict(zip(_AVERAGED_COLUMNS, averages)))
    return {"n_rows": int(len(rows)), "n_roads": int(rows.sd.nunique()),
            "n_segments": int(rows.segment_id.nunique()), **result}


def training_days_buckets(n_training_days):
    """Create reporting cohorts from training-only distinct observed dates."""
    values = pd.Series(n_training_days).reset_index(drop=True)
    numeric = pd.to_numeric(values, errors="coerce")
    if (numeric.dropna() < 0).any():
        raise ValueError("Training-day counts must be nonnegative.")
    result = pd.cut(numeric, [-1, 0, 2, 7, 14, 30, np.inf],
                    labels=["0 (unseen)", "1-2", "3-7", "8-14", "15-30", "31+"])
    return result.astype("string").fillna("unknown")


def per_road_metrics(rows, predictions, weights=None):
    """Return one error summary per road/direction, preserving supplied weights."""
    values = _row_metrics(rows, predictions)
    weight = _weights(rows, weights)
    values["sd"] = rows["sd"].to_numpy()
    values[_AVERAGED_COLUMNS] = values[_AVERAGED_COLUMNS].multiply(weight, axis=0)
    values["weight"] = weight
    grouped = values.groupby("sd", observed=True, sort=True)
    averages = grouped[_AVERAGED_COLUMNS].sum().div(grouped.weight.sum(), axis=0)
    averages["rmse"] = np.sqrt(averages.pop("mse"))
    averages["log1p_rmse"] = np.sqrt(averages.pop("log1p_mse"))
    keys = [name for name in ["sd", "segment_id", "direction", "borough", "road_type",
                             "n_training_days"] if name in rows]
    metadata = rows[keys].drop_duplicates("sd").set_index("sd")
    metadata["n_rows"] = rows.groupby("sd", observed=True).size()
    result = metadata.join(averages[METRIC_COLUMNS]).reset_index()
    if "n_training_days" in result:
        result["training_days_bucket"] = training_days_buckets(result.n_training_days)
    return result


def subgroup_metrics(rows, predictions,
                     group_columns=("borough", "source", "road_type", "training_days_bucket")):
    """Break down results; independently recompute equal-sd weights in each group.

    If rows contain ``n_training_days``, training-day buckets are added internally.
    A road can occur in multiple source groups; subgroup metrics consequently
    must not be summed or averaged to reconstruct the overall metric.
    """
    _validate_rows(rows)
    _prediction_array(predictions, len(rows))
    rows = rows.reset_index(drop=True).copy(deep=False)
    predictions = predictions.reset_index(drop=True)
    if "training_days_bucket" not in rows and "n_training_days" in rows:
        rows = rows.assign(training_days_bucket=training_days_buckets(rows.n_training_days))
    records = []
    for column in group_columns:
        if column not in rows:
            continue
        labels = rows[column].astype("string").fillna("<missing>")
        for label, indices in labels.groupby(labels, sort=True).groups.items():
            records.append({"group": column, "group_value": str(label),
                            **evaluate_predictions(rows.iloc[indices], predictions.iloc[indices])})
    return pd.DataFrame(records, columns=["group", "group_value", "n_rows", "n_roads", "n_segments",
                                          *METRIC_COLUMNS])


def paired_mae_bootstrap(rows, b1_predictions, b2_predictions, n_bootstrap=1000, seed=42,
                         weights=None):
    """Paired cluster bootstrap of equal-road MAE, sampling whole segments.

    Positive ``mae_improvement_b1_minus_b2`` means B2 has lower MAE. Each segment's
    directions are sampled together. Each replicate divides summed road errors by
    its sampled number of road/direction units, preserving equal-sd influence.
    The interval is a descriptive percentile bootstrap CI for this test cohort,
    rather than a claim of significance on independent traffic observations.
    """
    _validate_rows(rows)
    b1 = _prediction_array(b1_predictions, len(rows))[:, 1]
    b2 = _prediction_array(b2_predictions, len(rows))[:, 1]
    weight = _weights(rows, weights)
    if not isinstance(n_bootstrap, (int, np.integer)) or n_bootstrap < 2:
        raise ValueError("At least two bootstrap repetitions are required.")
    if rows.groupby("sd", observed=True).segment_id.nunique().ne(1).any():
        raise ValueError("Every road/direction must belong to exactly one segment.")
    y = rows.volume.to_numpy(dtype=float)
    loss = pd.DataFrame({"sd": rows.sd.to_numpy(), "segment_id": rows.segment_id.to_numpy(),
                         "b1": np.abs(y - b1) * weight, "b2": np.abs(y - b2) * weight,
                         "weight": weight})
    road = loss.groupby(["sd", "segment_id"], observed=True, sort=True)[["b1", "b2", "weight"]].sum()
    road[["b1", "b2"]] = road[["b1", "b2"]].div(road.weight, axis=0)
    road["improvement"] = road.b1 - road.b2
    segment = road.reset_index().groupby("segment_id", sort=True).agg(
        summed_improvement=("improvement", "sum"), n_roads=("sd", "size"))
    totals = segment.summed_improvement.to_numpy(dtype=float)
    denominators = segment.n_roads.to_numpy(dtype=float)
    rng = np.random.default_rng(seed)
    bootstrap = np.empty(n_bootstrap, dtype=float)
    # Batching bounds temporary memory even when this is used for a larger city.
    for start in range(0, n_bootstrap, 128):
        end = min(start + 128, n_bootstrap)
        draws = rng.integers(0, len(segment), size=(end - start, len(segment)))
        bootstrap[start:end] = totals[draws].sum(axis=1) / denominators[draws].sum(axis=1)
    lower, upper = np.quantile(bootstrap, [0.025, 0.975])
    return {"mae_b1": float(road.b1.mean()), "mae_b2": float(road.b2.mean()),
            "mae_improvement_b1_minus_b2": float(road.improvement.mean()),
            "ci95_lower": float(lower), "ci95_upper": float(upper),
            "n_bootstrap": int(n_bootstrap), "seed": int(seed),
            "n_segments": int(len(segment)), "n_roads": int(len(road)),
            "bootstrap_unit": "whole_segment", "ci_method": "percentile"}


def _markdown_table(frame, columns=None):
    if frame is None or len(frame) == 0:
        return "No rows available."
    data = frame if columns is None else frame[[column for column in columns if column in frame]]
    def display(value):
        if pd.isna(value):
            return "—"
        if isinstance(value, (float, np.floating)):
            return f"{value:,.4f}"
        return str(value).replace("|", "\\|").replace("\n", " ")
    header = "| " + " | ".join(map(str, data.columns)) + " |"
    divider = "| " + " | ".join(["---"] * len(data.columns)) + " |"
    body = ["| " + " | ".join(display(value) for value in record) + " |"
            for record in data.itertuples(index=False, name=None)]
    return "\n".join([header, divider, *body])


def write_markdown_report(path, metrics_frame, breakdown_frame, bootstrap_frame, notes=()):
    """Write a concise, portable report without requiring the tabulate package.

    All three frames may contain caller-provided ``scenario`` and ``baseline``
    identifiers. The caller owns versioned-path and overwrite policy.
    """
    path = Path(path)
    lines = ["# Plexa mobility: first two baselines", "", WEIGHTING_NOTE, "",
             "Volumes, errors and interval widths are vehicles/hour. Log metrics use log1p(volume). "
             "Coverage is a proportion; the target is 0.80. WIS combines the median and central "
             "80% interval, and penalizes both interval width and missed observations.", ""]
    lines.extend(f"- {note}" for note in notes)
    lines.extend(["", "## Overall test results", "", _markdown_table(metrics_frame), "",
                  "## Paired MAE comparison", "",
                  "Positive B1 minus B2 means B2 improves MAE. The 95% percentile intervals "
                  "resample whole segments, keeping both directions together. A confidence "
                  "interval crossing zero does not establish a consistent improvement.", "",
                  _markdown_table(bootstrap_frame), "", "## Subgroup results", "",
                  WEIGHTING_NOTE, "", _markdown_table(breakdown_frame), ""])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def plot_comparison(metrics_frame, path):
    """Save two plain comparison panels, importing optional matplotlib on demand.

    Input columns are ``scenario``, ``baseline``, ``mae`` and ``coverage_80``.
    The first two sorted baseline names are used if they do not start B1/B2;
    familiar global/scaled names are also recognized. No significance is inferred.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import PercentFormatter, StrMethodFormatter

    required = {"scenario", "baseline", "mae", "coverage_80"}
    if not required.issubset(metrics_frame.columns):
        raise ValueError(f"Plot inputs are missing: {sorted(required - set(metrics_frame.columns))}")
    if len(metrics_frame) == 0 or metrics_frame.baseline.nunique() != 2:
        raise ValueError("Comparison plot requires exactly two baselines.")
    if metrics_frame.duplicated(["scenario", "baseline"]).any():
        raise ValueError("Plot inputs contain duplicate scenario/baseline results.")
    scenarios = [name for name in ["within_road", "unseen_roads", "time", "season_transfer"]
                 if name in set(metrics_frame.scenario)]
    if not scenarios:
        raise ValueError("Comparison plot has no recognized evaluation scenarios.")
    names = sorted(metrics_frame.baseline.astype(str).unique())
    names.sort(key=lambda name: (0 if name.lower().startswith("b1") or "global" in name.lower()
                                 else 1, name))
    friendly = {"within_road": "Held-out days", "unseen_roads": "Unseen roads",
                "time": "Later years", "season_transfer": "Autumn → summer"}
    colors = ["#326B93", "#D18A39"]
    titles = ["Typical traffic volume error", "Central 80% interval coverage"]
    labels = ["B1 · Global profile", "B2 · Road-scaled profile"]
    x = np.arange(len(scenarios))
    with plt.rc_context({"font.family": "DejaVu Sans", "font.size": 10,
                         "axes.titlesize": 13, "axes.titleweight": "bold",
                         "axes.labelcolor": "#334155", "text.color": "#172B40",
                         "xtick.color": "#334155", "ytick.color": "#334155",
                         "figure.facecolor": "white", "axes.facecolor": "white"}):
        figure, axes = plt.subplots(1, 2, figsize=(12.8, 5.1), layout="constrained")
        for panel, metric in enumerate(["mae", "coverage_80"]):
            axis = axes[panel]
            for model, name in enumerate(names):
                part = metrics_frame.loc[metrics_frame.baseline.astype(str).eq(name)].set_index("scenario")
                values = part.reindex(scenarios)[metric].to_numpy(dtype=float)
                bars = axis.bar(x + (model - 0.5) * 0.34, values, 0.32,
                                color=colors[model], label=labels[model], zorder=3)
                formats = [(f"{value:.1%}" if metric == "coverage_80" else f"{value:,.1f}")
                           if np.isfinite(value) else "" for value in values]
                axis.bar_label(bars, labels=formats, padding=4, fontsize=8, color="#334155")
            axis.set_title(titles[panel], loc="left", pad=16)
            axis.set_xticks(x, [friendly[scenario] for scenario in scenarios])
            axis.tick_params(axis="both", length=0)
            axis.grid(axis="y", color="#E4E9EF", linewidth=0.8, zorder=0)
            axis.set_axisbelow(True)
            for spine in axis.spines.values():
                spine.set_visible(False)
            if metric == "mae":
                axis.set_ylabel("MAE (vehicles/hour) · lower is better", labelpad=10)
                axis.yaxis.set_major_formatter(StrMethodFormatter("{x:,.0f}"))
                axis.set_ylim(0, max(1.0, float(metrics_frame.mae.max()) * 1.20))
            else:
                axis.set_ylabel("Share of observations covered", labelpad=10)
                axis.yaxis.set_major_formatter(PercentFormatter(1.0))
                axis.set_ylim(0, 1.12)
                axis.axhline(0.8, color="#64748B", linestyle="--", linewidth=1.2, zorder=4)
                axis.text(0.99, 0.8, "80% target", transform=axis.get_yaxis_transform(),
                          ha="right", va="bottom", fontsize=9, color="#475569")
        axes[0].legend(frameon=False, loc="upper right", fontsize=9)
        figure.suptitle("Plexa mobility baselines", fontsize=17, fontweight="bold", x=0.01, ha="left")
        figure.supxlabel("Each road/direction contributes equally. Seasonal transfer uses a small cohort.",
                           fontsize=9, color="#64748B")
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        figure.savefig(path, dpi=180, facecolor="white")
        plt.close(figure)
    return path
