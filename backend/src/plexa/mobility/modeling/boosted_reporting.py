"""Equal-road regression R² and presentation of all three mobility baselines."""

from pathlib import Path

import numpy as np
import pandas as pd

try:
    from .baseline_evaluation import _markdown_table, _validate_rows, _weights
except ImportError:
    from baseline_evaluation import _markdown_table, _validate_rows, _weights


SCENARIOS = ["within_road", "unseen_roads", "time", "season_transfer"]
BASELINES = ["B1_global", "B2_road_scaled", "B3_gradient_boosted"]
SCENARIO_LABELS = {"within_road": "Held-out days", "unseen_roads": "Unseen roads",
                   "time": "Later years", "season_transfer": "Autumn → summer"}
BASELINE_LABELS = {"B1_global": "B1 · Global", "B2_road_scaled": "B2 · Road-scaled",
                   "B3_gradient_boosted": "B3 · Gradient-boosted"}


def weighted_r2(rows, predictions, weights=None):
    """Return R² with every road/direction equally weighted, matched positionally.

    The reference is the single weighted mean of test volumes. This is a pooled
    weighted regression R², not a mean of individual roads' R². It may be negative.
    For constant targets, return 1 for perfect predictions and 0 otherwise, using
    the conventional finite-score policy. Scenario weights must total one per sd.
    Only ``p50`` is required in predictions.
    """
    y = _validate_rows(rows)
    if not isinstance(predictions, pd.DataFrame) or "p50" not in predictions:
        raise ValueError("R² predictions require a DataFrame with a p50 column.")
    p = predictions.p50.to_numpy(dtype=float)
    if len(p) != len(y) or not np.isfinite(p).all():
        raise ValueError("R² needs one finite median prediction per observation.")
    weight = _weights(rows, weights)
    residual_sum = float(np.dot(weight, (y - p) ** 2))
    positive_targets = y[weight > 0]
    if np.all(positive_targets == positive_targets[0]):
        return 1.0 if residual_sum == 0 else 0.0
    mean = np.average(y, weights=weight)
    total_sum = float(np.dot(weight, (y - mean) ** 2))
    if total_sum == 0:
        return 1.0 if residual_sum == 0 else 0.0
    return float(1.0 - residual_sum / total_sum)


def plot_three_models(metrics_frame, path):
    """Save MAE and 80% interval-coverage bars for the four fixed test scenarios."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import PercentFormatter, StrMethodFormatter

    required = {"scenario", "baseline", "mae", "coverage_80"}
    if not required.issubset(metrics_frame.columns):
        raise ValueError(f"Plot inputs are missing: {sorted(required - set(metrics_frame.columns))}")
    if metrics_frame.duplicated(["scenario", "baseline"]).any():
        raise ValueError("Plot inputs contain duplicate scenario/baseline results.")
    unknown = set(metrics_frame.baseline) - set(BASELINES)
    if unknown:
        raise ValueError(f"Unrecognized baseline names: {sorted(unknown)}")
    scenarios = [scenario for scenario in SCENARIOS if scenario in set(metrics_frame.scenario)]
    if not scenarios or set(metrics_frame.baseline) != set(BASELINES):
        raise ValueError("The comparison chart needs all three recognized baselines.")
    expected = pd.MultiIndex.from_product([scenarios, BASELINES], names=["scenario", "baseline"])
    table = metrics_frame.set_index(["scenario", "baseline"]).reindex(expected)
    values = table[["mae", "coverage_80"]].to_numpy(dtype=float)
    if not np.isfinite(values).all() or (values[:, 0] < 0).any():
        raise ValueError("Every plotted scenario/model needs a finite nonnegative MAE and coverage.")
    if ((values[:, 1] < 0) | (values[:, 1] > 1)).any():
        raise ValueError("Coverage must be a proportion between zero and one.")
    colors = ["#71859A", "#C38C42", "#2D7F82"]
    x = np.arange(len(scenarios))
    with plt.rc_context({"font.family": "DejaVu Sans", "font.size": 10,
                         "axes.titlesize": 13, "axes.titleweight": "bold",
                         "axes.labelcolor": "#334155", "text.color": "#172B40",
                         "xtick.color": "#334155", "ytick.color": "#334155",
                         "figure.facecolor": "white", "axes.facecolor": "white"}):
        fig, axes = plt.subplots(1, 2, figsize=(14, 5.4), layout="constrained")
        titles = ["Typical traffic volume error", "Central 80% interval coverage"]
        for panel, metric in enumerate(["mae", "coverage_80"]):
            ax = axes[panel]
            for model_index, model in enumerate(BASELINES):
                model_values = table.xs(model, level="baseline")[metric].reindex(scenarios).to_numpy()
                bars = ax.bar(x + (model_index - 1) * 0.25, model_values, width=0.23,
                              label=BASELINE_LABELS[model], color=colors[model_index], zorder=3)
                text = [f"{v:.0%}" if metric == "coverage_80" else f"{v:,.0f}" for v in model_values]
                ax.bar_label(bars, labels=text, padding=4, fontsize=8, color="#334155")
            ax.set_title(titles[panel], loc="left", pad=18)
            ax.set_xticks(x, [SCENARIO_LABELS[scenario] for scenario in scenarios])
            ax.tick_params(axis="both", length=0)
            ax.grid(axis="y", color="#E4E9EF", linewidth=0.8, zorder=0)
            ax.set_axisbelow(True)
            for spine in ax.spines.values():
                spine.set_visible(False)
            if metric == "mae":
                ax.set_ylabel("MAE (vehicles/hour) · lower is better", labelpad=10)
                ax.yaxis.set_major_formatter(StrMethodFormatter("{x:,.0f}"))
                ax.set_ylim(0, max(1.0, float(table.mae.max()) * 1.25))
            else:
                ax.set_ylabel("Share of observations covered", labelpad=10)
                ax.yaxis.set_major_formatter(PercentFormatter(1.0))
                ax.set_ylim(0, 1.16)
                ax.axhline(0.8, color="#64748B", linestyle="--", linewidth=1.2, zorder=4)
                ax.text(0.99, 0.8, "80% target", transform=ax.get_yaxis_transform(),
                        ha="right", va="bottom", fontsize=9, color="#475569")
        handles, labels = axes[0].get_legend_handles_labels()
        fig.legend(handles, labels, frameon=False, loc="upper right", ncols=3,
                   fontsize=9, bbox_to_anchor=(0.99, 1.0))
        fig.suptitle("Plexa mobility · three baseline models", fontsize=17,
                     fontweight="bold", x=0.01, ha="left")
        fig.supxlabel("Equal weight per road/direction. Seasonal transfer uses a small cohort.",
                      fontsize=9, color="#64748B")
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(path, dpi=180, facecolor="white")
        plt.close(fig)
    return path


def _importance_table(feature_importance):
    """Normalize gain in each scenario before averaging, filling absent features with zero."""
    if feature_importance is None or len(feature_importance) == 0:
        return None, None
    feature_importance = pd.DataFrame(feature_importance).copy()
    if "feature" not in feature_importance:
        raise ValueError("Feature importance needs a feature column.")
    gain_column = next((name for name in ["gain", "splitgain", "split_gain", "importance_gain",
                                          "importance", "split_count"]
                        if name in feature_importance), None)
    if gain_column is None:
        raise ValueError("Feature importance needs gain, splitgain, importance or split_count.")
    if "scenario" not in feature_importance:
        feature_importance["scenario"] = "all"
    feature_importance[gain_column] = pd.to_numeric(feature_importance[gain_column], errors="raise")
    if not np.isfinite(feature_importance[gain_column]).all() or (feature_importance[gain_column] < 0).any():
        raise ValueError("Feature importance values must be finite and nonnegative.")
    gains = feature_importance.groupby(["scenario", "feature"], observed=True)[gain_column].sum().unstack(fill_value=0)
    fractions = gains.div(gains.sum(axis=1).replace(0, np.nan), axis=0).fillna(0)
    summary = fractions.mean(axis=0).sort_values(ascending=False).head(15)
    table = pd.DataFrame({"Feature": summary.index,
                          "Mean within-scenario share": [f"{value:.1%}" for value in summary.to_numpy()]})
    return table, gain_column


def write_report(path, metrics, comparisons, feature_importance=None, notes=()):
    """Write concise results for B1, B2 and B3; caller controls output versioning.

    Metrics may name the R² column ``weighted_r2`` or ``r2``. Paired comparison
    fields from ``paired_mae_bootstrap`` are interpreted as reference minus B3;
    callers should identify the reference in ``reference_baseline`` or ``reference``.
    Feature gains describe fitted model use and are never interpreted causally.
    """
    metrics = pd.DataFrame(metrics).copy()
    if "weighted_r2" not in metrics and "r2" in metrics:
        metrics = metrics.rename(columns={"r2": "weighted_r2"})
    ordered = ["scenario", "baseline", "mae", "rmse", "weighted_r2", "log1p_mae",
               "coverage_80", "mean_interval_width"]
    score = metrics[[name for name in ordered if name in metrics]].copy()
    if "coverage_80" in score:
        score["coverage_80"] = score.coverage_80.map(lambda value: f"{value:.1%}")
    score = score.rename(columns={"scenario": "Evaluation", "baseline": "Model",
                                  "mae": "MAE", "rmse": "RMSE", "weighted_r2": "Weighted R²",
                                  "log1p_mae": "Log MAE", "coverage_80": "80% coverage",
                                  "mean_interval_width": "Mean interval width"})
    if "Evaluation" in score:
        score["Evaluation"] = score.Evaluation.map(lambda value: SCENARIO_LABELS.get(value, value))
    if "Model" in score:
        score["Model"] = score.Model.map(lambda value: BASELINE_LABELS.get(value, value))
    comparisons = pd.DataFrame(comparisons).copy() if comparisons is not None else pd.DataFrame()
    reference_col = next((name for name in ["reference_baseline", "reference", "reference_model",
                                           "baseline_reference"] if name in comparisons), None)
    comparison_columns = ["scenario", *([reference_col] if reference_col else []),
                          "mae_b1", "mae_b2", "mae_improvement_b1_minus_b2",
                          "ci95_lower", "ci95_upper"]
    comparison = comparisons[[name for name in comparison_columns if name in comparisons]].rename(
        columns={"scenario": "Evaluation", reference_col: "Reference", "mae_b1": "Reference MAE",
                 "mae_b2": "B3 MAE", "mae_improvement_b1_minus_b2": "MAE improvement",
                 "ci95_lower": "95% CI lower", "ci95_upper": "95% CI upper"})
    if "Evaluation" in comparison:
        comparison["Evaluation"] = comparison.Evaluation.map(lambda value: SCENARIO_LABELS.get(value, value))
    if "Reference" in comparison:
        comparison["Reference"] = comparison.Reference.map(lambda value: BASELINE_LABELS.get(value, value))
    importance, gain_column = _importance_table(feature_importance)
    lines = ["# Plexa mobility: three baseline models", "",
             "Each evaluation uses the same frozen test observations for all models. Each road/direction "
             "has equal weight, regardless of how many hours were recorded.", "",
             "MAE, RMSE and interval widths are in vehicles/hour; log MAE uses log1p(volume). "
             "Weighted R² compares squared prediction errors with a single equal-road weighted mean "
             "of the test volumes. Negative values mean worse squared error than that mean. "
             "Constant targets score 1 for perfect predictions and 0 otherwise.", "",
             "The nominal interval coverage is 80%. Compare coverage together with interval width; "
             "a wider interval alone does not establish a better model.", ""]
    if notes:
        lines.extend(["## Method and limitations", ""])
        lines.extend(f"- {note}" for note in notes)
        lines.append("")
    lines.extend(["## Held-out results", "", _markdown_table(score), "",
                  "## Paired MAE improvement over each reference", "",
                  "Positive reference minus B3 values mean lower MAE for B3. The percentile 95% "
                  "confidence intervals resample whole segments, keeping their directions together. "
                  "An interval crossing zero does not establish a consistent improvement. "
                  "These intervals describe the observed test cohort; they do not account for every "
                  "source of model or future-city uncertainty.", "", _markdown_table(comparison), ""])
    if importance is not None:
        lines.extend(["## Features used by the fitted boosted model", "",
                      f"Top features by {gain_column}, normalized within each scenario and then "
                      "averaged equally across scenarios. Absent features contribute zero. These "
                      "values describe how the fitted trees use the available predictors; they do "
                      "not show causal effects and correlated predictors can share or exchange importance.",
                      "", _markdown_table(importance), ""])
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
