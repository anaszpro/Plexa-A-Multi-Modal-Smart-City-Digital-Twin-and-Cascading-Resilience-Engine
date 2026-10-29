# Plexa mobility evaluation splits

Status: validation passed.

Parent observations: 1,412,050. Seed: 42.

The four columns define independent experiments, not four pieces of one partition.

| Evaluation | Training rows | Test rows | Excluded rows | Test road/direction units |
|---|---:|---:|---:|---:|
| within_road | 1,067,162 | 312,488 | 32,400 | 4,671 |
| unseen_roads | 1,130,186 | 281,864 | 0 | 1,175 |
| time | 391,840 | 258,867 | 761,343 | 1,154 |
| season_transfer | 2,304 | 3,624 | 1,406,122 | 17 |

## within_road

Random held-out local dates per segment, shared across directions; at least 3 training dates and 1 test date per road/direction; otherwise excluded.

Training dates: ['2000-01-01', '2026-02-02']. Test dates: ['2000-01-02', '2026-02-01'].

Training segments: 3,453; test segments: 3,453.

| Test borough | Rows |
|---|---:|
| Brooklyn | 86,414 |
| Queens | 83,711 |
| Bronx | 61,773 |
| Manhattan | 55,644 |
| Staten Island | 22,024 |
| unknown | 2,922 |

## unseen_roads

Approximately 20% of whole segments withheld, stratified by borough with missing borough as a separate stratum; every direction and date stays together.

Training dates: ['2000-01-01', '2026-02-03']. Test dates: ['2006-09-05', '2026-02-03'].

Training segments: 3,567; test segments: 892.

| Test borough | Rows |
|---|---:|
| Brooklyn | 79,800 |
| Queens | 76,857 |
| Bronx | 54,644 |
| Manhattan | 46,798 |
| Staten Island | 21,091 |
| unknown | 2,674 |

## time

Matched road/direction units only; train through 2018, test from 2019; at least 3 observed dates on each side.

Training dates: ['2007-04-02', '2018-12-24']. Test dates: ['2019-01-04', '2026-01-09'].

Training segments: 775; test segments: 775.

| Test borough | Rows |
|---|---:|
| Queens | 66,078 |
| Brooklyn | 65,958 |
| Manhattan | 56,963 |
| Bronx | 46,053 |
| Staten Island | 21,872 |
| unknown | 1,943 |

## season_transfer

Matched road/direction/year pairs with >=3 full 24-hour days in each season. Only full autumn days train and full summer days test. Restricted paired-cohort sensitivity test; not a chronological forecast.

Training dates: ['2010-09-01', '2020-09-18']. Test dates: ['2010-08-27', '2020-08-31'].

Training segments: 15; test segments: 15.

| Test borough | Rows |
|---|---:|
| Staten Island | 1,464 |
| Queens | 1,296 |
| Brooklyn | 768 |
| Manhattan | 96 |

## Seasonal evidence

Only 17 road/year pairs qualify. Treat season-transfer scores as indicative; do not infer citywide seasonal accuracy.

## Training and evaluation rules

- Each scenario is an independent experiment. Never combine their assignments into one split.
- Join assignments to the parent modelling dataset on the four key columns, one-to-one.
- model_row is a convenience offset for this exact input file, not a model feature.
- Use split_<scenario> and w_<scenario>; excluded rows have zero weight and must be removed.
- Weights sum to one per road/direction separately within train and test.
- Never use n_rows_sd/n_days_sd/n_years_sd/n_seasons_sd/w_road from the full snapshot as predictors.
- All target encodings, imputation and learned transforms must fit on training data only.
- Road-history predictors for training rows must be out-of-fold, grouped by whole days; all directions of a segment/date must share a fold. Cold-start validation requires segment folds.
- Do not tune or calibrate intervals on test rows. Use grouped folds inside training data.
- Unseen-segment performance does not establish transfer to entirely unseen corridors or regions.
- Within-road and season-transfer results do not establish forecasting performance.

No model selection, interval calibration or tuning has been performed on test data.
