# Mobility

This module estimates typical vehicles/hour with p10, p50 and p90 predictions.
The saved fits evaluate three baselines on four independent splits. They do not
directly estimate speed, congestion, road closures or incident impacts.

## Files you work with

| Files | Purpose |
| --- | --- |
| `modeling/build_model_table.py` | Read the checked PostgreSQL tables and build the versioned modeling Parquet |
| `modeling/create_splits.py` | Create four independent train/test assignments without reading target values |
| `modeling/mobility_baselines.py` | Global and road-scaled profile models |
| `modeling/gradient_boosted.py` | LightGBM quantiles, feature encoding and B2 history features |
| `modeling/train_baselines.py` | Fit/evaluate B1 and B2 |
| `modeling/train_gradient_boosted.py` | Fit/evaluate B3 using saved B2 training-history outputs |
| `modeling/baseline_evaluation.py`, `boosted_reporting.py` | Metrics, comparisons and reports |
| `modeling/baseline_tracking.py` | Explicit logging to the local MLflow database |
| `modeling/verify_gradient_boosted_results.py` | Check frozen data, model artifacts, metrics, reloads, native boosters and tracking |
| Four `modeling/test_*.py` files | Model, feature-leakage, evaluation and reporting behavior checks |
| `modeling/requirements.txt` | One Python 3.13 environment for preparation, training and inspection |
| `database/` | Original database load/correction utilities, a read-only verifier and a local dump |

The four notebooks are in [notebooks/mobility](../../../../notebooks/mobility/).
Inputs are in [data/mobility](../../../../data/mobility/) and results are in
[outputs/mobility](../../../../outputs/mobility/). These are real root directories.

## Environment

From the repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/src/plexa/mobility/modeling/requirements.txt
$mobilityPython = (Resolve-Path .venv/Scripts/python.exe).Path
```

Use this interpreter in your notebook editor too. The preparation dependencies
include GeoPandas, Pyogrio and PyProj. The environment does not inherit packages
from the old workspace or the system Python installation.

## Verify the preserved baseline

From the repository root:

```powershell
& $mobilityPython -B backend/src/plexa/mobility/modeling/verify_gradient_boosted_results.py

Push-Location backend/src/plexa/mobility
try {
    & $mobilityPython -B -m unittest modeling.test_mobility_baselines modeling.test_baseline_evaluation modeling.test_gradient_boosted modeling.test_boosted_reporting -v
} finally {
    Pop-Location
}
```

Verification is read-only. It recomputes metrics from saved predictions, compares
model predictions with the saved rows, checks native LightGBM parity and inspects
MLflow through read-only SQLite. It does not retrain models or access PostgreSQL.

Frozen manifests retain their original source hashes. Three path-only changes
to the training/tracking code are recorded in the existing local
`outputs/mobility/mlflow_relocation.json`. The verifier checks both the exact
approved current bytes and the original source in Git commit
`e4fcf2f4889c9c0ac08103623b19c32cec1b5d90`. Model and evaluation implementations
remain byte-identical. Other source changes are rejected rather than silently
accepted.

## Data preparation and database boundary

1. `ATVC_preprocessing.ipynb` cleans automated traffic observations and produces
   `auto_hourly_v4.parquet`.
2. `HTVC_preprocessing.ipynb` cleans historical observations and produces
   `hist_hourly_v2.parquet`.
3. `Canonical_table.ipynb` joins the sources and creates initial combined/road tables.
4. `Checks.ipynb` contains real corrections as well as checks. It produces the
   final `combined_hourly_v3.parquet` and `segment_table_v4.parquet`.
5. PostgreSQL holds the checked `hourly`, `hourly_dst_flagged` and `segments`
   tables. `build_model_table.py` reads that database in a read-only transaction
   and records hashes of the final Parquet archives.

Notebook input paths now use the clone's root. Two pre-existing syntax typos in
the automated notebook were repaired. Saved notebook outputs describe earlier
runs; migration did not rerun the raw-data preparation. Preparation notebooks can
overwrite their named outputs, so use a deliberate new data version when
rebuilding rather than overwriting the preserved snapshot.

The original database sequence is schema setup, loading combined v2/segments v3,
then the corrections in `fix_29555.sql` and `fix_borough.sql`.
`sync_parquet.py` contains equivalent final Parquet corrections already present
in `Checks.ipynb`. Keep these historical steps together until a future rebuild
has been explicitly checked.

`setup_plexa.sql` drops tables and `load_plexa.py` appends data. Neither is a
migration/startup command. `check_plexa.py` expects the earlier 1,411,799-row
eligibility count; use `verify_starting_point.py` for the corrected snapshot.
The dump's restore has not been tested in this migration.

Database utilities require `PLEXA_DB_URL`; no credentials are embedded in code:

```powershell
$env:PLEXA_DB_URL = "postgresql+psycopg2://USER:PASSWORD@HOST:PORT/plexa"
& $mobilityPython -B backend/src/plexa/mobility/database/verify_starting_point.py
```

## Modeling and training

The preserved modeling table has 1,412,050 eligible observations. Its timestamp
identity is UTC; calendar fields use America/New_York. Keep its snapshot JSON and
the split Parquet/snapshot together. Training from these files needs no database.

The four independent evaluations are within-road dates, unseen roads,
chronological transfer (train through 2018), and a small same-year autumn-to-summer
sensitivity test. Do not combine them into one split or tune against their tests.

B1 is a road-balanced global hourly profile. B2 adds road/group history and
training-only out-of-fold residual calibration. B3 fits three LightGBM quantiles
on log1p(volume), using saved B2 out-of-fold history for training and its fitted
B2 history model at prediction time.

For an intentional new experiment, from the repository root:

```powershell
& $mobilityPython -B backend/src/plexa/mobility/modeling/train_baselines.py --output outputs/mobility/baselines_v2
& $mobilityPython -B backend/src/plexa/mobility/modeling/train_gradient_boosted.py --output outputs/mobility/gradient_boosted_v2
```

Each runner refuses an existing output directory. The B3 runner currently reads
the frozen `baselines_v1` parent even when its output directory is named v2.
Changing a parent dataset/split/baseline version is an explicit code/configuration
change; an output name alone does not select new training inputs.

The table and split builders likewise refuse to overwrite their outputs.
Use explicit new output paths when rebuilding. B1/B2 and B3 methods, parameters,
uncertainty limitations and measured results are recorded in their frozen reports:

- [B1/B2 report](../../../../outputs/mobility/baselines_v1/baseline_report.md)
- [Three-model report](../../../../outputs/mobility/gradient_boosted_v1/boosted_report.md)
- [Metrics](../../../../outputs/mobility/gradient_boosted_v1/all_models_metrics.csv)
- [Benchmark checks](../../../../outputs/mobility/gradient_boosted_v1/benchmark_checks.json)

## Saved models and the next integration step

There are twelve evaluation pickles: three baselines across four scenarios.
B3 also saves twelve native quantile boosters. Its pickle includes the encoder,
history model and learned history counts; native boosters alone are insufficient
for the complete prediction workflow.

Pickles require `modeling.mobility_baselines` and `modeling.gradient_boosted`
to be importable. The existing runners/verifier arrange that import path.
The prediction method accepts prepared rows and returns p10/p50/p90 in
vehicles/hour. A final serving fit and a road/time input adapter remain to be
developed before backend/fusion integration.

B3 improves unseen-road point accuracy but fails the agreed interval-coverage
criterion; B2 performs slightly better on the chronological point metric.

## MLflow

From the repository root:

```powershell
& $mobilityPython -m mlflow ui --backend-store-uri sqlite:///outputs/mobility/mlflow.db --host 127.0.0.1 --port 5000
```

The twelve preserved runs use the copied artifacts under this clone. Local
tracking files are ignored by Git. Keep the database, logged artifact directory
and relocation record together. Moving the clone again requires updating its
absolute MLflow artifact locations; ordinary code/data paths are project-relative.
