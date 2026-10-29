# Plexa mobility: three baseline models

Each evaluation uses the same frozen test observations for all models. Each road/direction has equal weight, regardless of how many hours were recorded.

MAE, RMSE and interval widths are in vehicles/hour; log MAE uses log1p(volume). Weighted R² compares squared prediction errors with a single equal-road weighted mean of the test volumes. Negative values mean worse squared error than that mean. Constant targets score 1 for perfect predictions and 0 otherwise.

The nominal interval coverage is 80%. Compare coverage together with interval width; a wider interval alone does not establish a better model.

## Method and limitations

- The four scenarios are independent experiments on the frozen model_table_v1 and model_splits_v1.
- B3 uses LightGBM quantile regression on log1p(volume), predicting q10/q50/q90. Fixed settings:300 trees,31 leaves, learning_rate0.05,min_child_samples100,lambda1,max_bin127,4 CPU threads.
- There is no parameter search, early stopping or test-based interval calibration. The three log-quantiles are sorted before expm1 to resolve crossings; raw crossing fractions are recorded.
- Each road/direction receives equal training influence. Fit weights are normalized to mean one to keep regularization scale stable. All test metrics use the original equal-road weights.
- History-profile features for training rows come exclusively from saved B2 grouped OOF predictions. Test history comes from B2 fitted only on that scenario's complete training partition. Training history-day counts use each OOF fit subset; test counts use complete training only.
- Calendar, holiday, borough, lane, width, speed, street direction, routing codes, coordinates and observation source are available features. Raw IDs, dates, target values, residuals, and full-snapshot coverage statistics are excluded from the trees.
- Road attributes remain missing when unavailable. Category vocabularies are learned on training rows only; unseen categories map to missing. Native LightGBM missing-value handling is used.
- Source is observation provenance and must be supplied or deliberately defined for future serving. The stored covid flag is a calendar-context indicator, not a causal estimate of pandemic impact.
- The time test is chronological in targets, but LION attributes are from the available release, not point-in-time historical street vintages. It is not a complete historical deployment backtest.
- Season transfer uses17 road/direction/year pairs (15 segments). Autumn is later than the same-year summer test; this is a small retrospective transfer check, not a future forecast.
- Coverage is empirical. A nominal80% interval should cover about80%, but there is no guarantee, especially under changes across years or seasons.
- R2 uses the global equal-road weighted test mean and may be negative. R2 is not accuracy. Subgroup metrics independently rebalance roads inside each subgroup.
- Feature importance is median-model training split gain, not causal influence. Paired MAE confidence intervals resample whole segments with both directions together.

## Held-out results

| Evaluation | Model | MAE | RMSE | Weighted R² | Log MAE | 80% coverage | Mean interval width |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Held-out days | B1 · Global | 325.8331 | 710.2669 | -0.0093 | 0.8728 | 80.0% | 935.8031 |
| Held-out days | B2 · Road-scaled | 117.1952 | 289.5928 | 0.8322 | 0.2843 | 85.6% | 383.4910 |
| Unseen roads | B1 · Global | 352.6527 | 764.0875 | -0.0370 | 0.9364 | 77.7% | 872.6518 |
| Unseen roads | B2 · Road-scaled | 314.2132 | 680.9527 | 0.1764 | 0.8095 | 78.9% | 834.9208 |
| Later years | B1 · Global | 196.0119 | 367.2413 | 0.1267 | 0.7050 | 81.3% | 691.4800 |
| Later years | B2 · Road-scaled | 102.9319 | 238.5767 | 0.6314 | 0.3491 | 66.3% | 235.7298 |
| Autumn → summer | B1 · Global | 269.6499 | 528.1893 | 0.0417 | 0.7861 | 76.3% | 760.7467 |
| Autumn → summer | B2 · Road-scaled | 124.4054 | 248.2540 | 0.7883 | 0.3641 | 90.8% | 624.1020 |
| Held-out days | B3 · Gradient-boosted | 81.2158 | 194.3133 | 0.9245 | 0.2203 | 82.8% | 278.6155 |
| Unseen roads | B3 · Gradient-boosted | 201.6303 | 428.9976 | 0.6731 | 0.5747 | 71.3% | 505.9849 |
| Later years | B3 · Gradient-boosted | 107.2738 | 243.7166 | 0.6154 | 0.3486 | 57.4% | 185.7590 |
| Autumn → summer | B3 · Gradient-boosted | 65.7780 | 126.0529 | 0.9454 | 0.2172 | 68.5% | 168.6771 |

## Paired MAE improvement over each reference

Positive reference minus B3 values mean lower MAE for B3. The percentile 95% confidence intervals resample whole segments, keeping their directions together. An interval crossing zero does not establish a consistent improvement. These intervals describe the observed test cohort; they do not account for every source of model or future-city uncertainty.

| Evaluation | Reference | Reference MAE | B3 MAE | MAE improvement | 95% CI lower | 95% CI upper |
| --- | --- | --- | --- | --- | --- | --- |
| Held-out days | B1 · Global | 325.8331 | 81.2158 | 244.6173 | 230.7816 | 259.1353 |
| Held-out days | B2 · Road-scaled | 117.1952 | 81.2158 | 35.9794 | 32.3645 | 39.6367 |
| Unseen roads | B1 · Global | 352.6527 | 201.6303 | 151.0224 | 126.2223 | 180.7438 |
| Unseen roads | B2 · Road-scaled | 314.2132 | 201.6303 | 112.5829 | 90.8644 | 137.2442 |
| Later years | B1 · Global | 196.0119 | 107.2738 | 88.7381 | 77.9685 | 100.2543 |
| Later years | B2 · Road-scaled | 102.9319 | 107.2738 | -4.3419 | -6.5168 | -2.2151 |
| Autumn → summer | B1 · Global | 269.6499 | 65.7780 | 203.8719 | 68.4539 | 398.1611 |
| Autumn → summer | B2 · Road-scaled | 124.4054 | 65.7780 | 58.6274 | 16.2254 | 127.1504 |

## Features used by the fitted boosted model

Top features by gain, normalized within each scenario and then averaged equally across scenarios. Absent features contribute zero. These values describe how the fitted trees use the available predictors; they do not show causal effects and correlated predictors can share or exchange importance.

| Feature | Mean within-scenario share |
| --- | --- |
| history_profile_log | 63.7% |
| lat | 3.0% |
| truck_route_type | 2.8% |
| posted_speed | 2.7% |
| streetwidth_max | 2.6% |
| lon | 2.5% |
| shape_length | 2.3% |
| hour | 2.1% |
| hour_cos | 1.9% |
| trafdir | 1.6% |
| number_travel_lanes | 1.5% |
| year | 1.4% |
| dow | 1.4% |
| direction | 1.3% |
| history_days | 1.2% |
