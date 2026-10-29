# Plexa mobility modelling table v1

Status: validation passed.

- Observations: 1,412,050
- Columns: 48
- Segments: 4,459
- Road/direction units: 5,899
- Local time range: 2000-01-01T01:00:00 to 2026-02-03T23:00:00
- Median observed dates per road/direction: 9
- Holiday observations: 46,999 (3.33%)
- Adjacent-to-holiday observations: 119,821
- Sum of road weights: 5,899

Calendar: Python holidays, US/NY public holidays, including observed dates. All calendar fields use America/New_York local time. Holiday-adjacent means the previous or next date is a holiday; it can also be true on a holiday. This is not a school-holiday or NYC event calendar.

## Road features with missing values

Missing values were preserved.

| Feature | Missing rows | Share |
|---|---:|---:|
| borough | 12,813 | 0.91% |
| posted_speed | 43,416 | 3.07% |
| number_travel_lanes | 30,225 | 2.14% |
| number_park_lanes | 16,953 | 1.20% |
| number_total_lanes | 30,462 | 2.16% |
| streetwidth_min | 30,225 | 2.14% |
| streetwidth_max | 30,225 | 2.14% |
| shape_length | 16,953 | 1.20% |
| trafdir | 18,038 | 1.28% |
| truck_route_type | 16,953 | 1.20% |
| snow_priority | 19,770 | 1.40% |
| bikelane | 16,953 | 1.20% |
| lon | 16,953 | 1.20% |
| lat | 16,953 | 1.20% |

## Holiday observations

| Holiday label | Rows |
|---|---:|
| Columbus Day | 18,348 |
| Veterans Day | 8,335 |
| Election Day | 7,271 |
| Susan B. Anthony Day | 3,182 |
| Lincoln's Birthday | 2,017 |
| Veterans Day (observed) | 1,866 |
| Martin Luther King Jr. Day | 1,042 |
| Memorial Day | 985 |
| Thanksgiving Day | 687 |
| Independence Day | 605 |
| Lincoln's Birthday (observed) | 576 |
| Labor Day | 556 |
| Washington's Birthday | 538 |
| Juneteenth National Independence Day | 384 |
| Juneteenth National Independence Day (observed) | 191 |
| New Year's Day | 163 |
| Susan B. Anthony Day; Washington's Birthday | 113 |
| Independence Day (observed) | 92 |
| Christmas Day | 48 |

## Use in training

- No train/test splits or target-derived road-history predictors are created here.
- Coverage columns are full-snapshot diagnostics, not predictors.
- Recompute inverse-road weights separately within each training/evaluation partition.
- Compute all learned road-history features using training/OOF data only.
- Static LION attributes describe the available street-network release, not historical vintages.

The table is a modelling input, not a trained model. No evaluation splits have been assigned. No model has been fitted. Source data and database were not modified.
