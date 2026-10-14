# Climate baseline preparation

Status on 2026-10-15 (Asia/Karachi): preparation is in progress. No model has been
trained. The full corrected rainfall rebuild is running; the follow-on process
will build monthly features and stop at a quality-review report. This is not yet
a complete, frozen training dataset.

## What is prepared and verified

- Read-only PostgreSQL snapshot: 184,473 normal records and three time-review
  records, covering 2020-10-14 through 2026-10-07 exclusive, New York dates.
- All 73 monthly windows agree with NYC Open Data at verification time, including
  the fields used in labeling and mapping. This is not a guarantee of complete
  real-world reporting or an upstream transactionally frozen snapshot.
- 21,725 located reports become 20,362 distinct positive segment-hours.
- Zero-label uncertainty masks: 739 global hours and 32 segment/hour pairs.
  Positive evidence takes precedence over these masks. One qualifying report has
  an unresolved local timestamp; its entire local date is masked for zeros.
- 91,895 physical segments have metric radar assignments and valid sampled DEM
  features. Midpoint borough counts: Queens 35,843; Brooklyn 21,601; Staten Island
  13,360; Bronx 12,769; Manhattan 8,247; outside polygon boundaries 75. This is
  coverage of the supplied OSM drive-network snapshot, not proof of all NYC roads.
- A complete 24-hour smoke dataset has 41 positive, 2,113,552 zero, and 91,887
  unknown rows; no duplicate segment/hour keys. Its training sample retains all
  41 positives and 388 negatives with inverse inclusion weight 5,000.
- Six unit/integration tests pass. These test actual temporal and label semantics,
  including 25-hour DST days, missing-vs-dry rainfall, lagged availability,
  ambiguous location masks, and positive precedence.

## Scientific contract

The target is a qualifying **311 flooding report in the next hour**, not measured
water depth or verified road safety. Target interval: `[t, t + 1 hour)` in UTC.
Zeros mean no report observed with verified ingestion and no unresolved evidence.
Unknown labels are excluded. Multiple reports in one segment/hour produce one
positive example, with the report count retained for auditing only.

Static features use EPSG:32618 for metre distances. SRTM is sampled along each
segment at intervals no greater than 20 metres. Mean elevation, terrain relief,
and absolute endpoint elevation difference divided by metric length are terrain
proxies; the endpoint grade is not a surveyed road slope. The current street
snapshot is used for historic examples. These limitations must accompany results.

Hourly rainfall windows are 1, 3, 6, and 24 consecutive hours; every constituent
cell/hour must be observed. Missing files/cells remain missing, never zero.
Radar feature availability uses an explicit three-hour lag: the historical Pass 2
product had nominal 120-minute latency, with a further hour allowed for processing.
Actual archive arrival timestamps are unavailable, so this remains a retrospective
availability assumption, not a demonstrated live forecasting service.

Sources for latency:
- [NOAA v12.0 upgrade specification](https://www.nco.ncep.noaa.gov/pmb/changes/MRMS_SCN_v12.0_Upgrade_Supplemental.pdf)
- [Current NOAA product table](https://www.nssl.noaa.gov/projects/mrms/operational/tables.php)

Train/validation/test are chronological, separated by seven-day gaps. The final
checker looks for six consecutive citywide nearly-dry rainfall hours in each gap
as a storm-separation diagnostic; this is not an independently verified storm catalog.
Training keeps all eligible positives and a deterministic 1-in-5,000 negative
sample, with inverse inclusion weights. Validation/test are not undersampled.
Sampling weights must be used appropriately in training; calibration must be
assessed on naturally distributed validation data, not the sampled training set.

## Files and execution order

Use `/opt/anaconda3/envs/plexa-climate/bin/python` (Python 3.11). Installed Python
package versions are recorded in `requirements-climate-preparation.txt`; native
GDAL/PROJ/ecCodes compatibility must also be maintained.

1. `scripts/export_311_snapshot.py`: immutable database export, read-only access.
2. `scripts/verify_311_source_coverage.py`: resumable monthly API comparisons.
3. `scripts/build_hourly_report_labels.py`: sparse positives and unknown masks.
4. `scripts/build_baseline_street_features.py`: metric static features.
5. `scripts/fetch_historical_radar.py`: corrected complete-grid archive rebuild.
6. `scripts/build_baseline_weather.py`: lagged rainfall features for a time range.
7. `scripts/build_baseline_dataset.py`: DuckDB views joining immutable inputs.
8. `scripts/finish_baseline_preparation.py`: monthly weather build, lazy dataset,
   split counts and boundary checks; no training call exists in this process.

The follow-on process currently waits for the running downloader's completion
summary. Its logs are `/tmp/plexa-preparation-finish.log`; downloader logs are
`/tmp/plexa-radar-rebuild.log`. These jobs need the machine to stay running. They
are ordinary local processes, not installed background services or scheduled tasks.

Progress is saved in `data/processed/baseline_preparation_status.json`.
If interrupted, resume the rainfall command (valid files are checked and skipped):

```bash
python -u scripts/fetch_historical_radar.py \
  --start 2020-10-13 --end 2026-10-08 \
  --output-dir data/raw/radar_parquet_v2
```

Then resume completion without the wait flag:

```bash
python -u scripts/finish_baseline_preparation.py
```

Do not run duplicate download jobs into the same directory. Generated immutable
artifacts refuse replacement. If an input changes or a previously missing source
file arrives, affected derived artifacts require a new version; do not reuse old
coverage or manifests silently. The future full dataset uses the same snapshot
cutoff as the existing labels; it does not quietly extend to today's reports.

The final normalized dataset is `data/processed/baseline_dataset_v1/dataset.duckdb`.
Its Parquet inputs must stay at their recorded locations. Query full evaluation
views with date filters, rather than loading billions of rows into a DataFrame:

```sql
SELECT * FROM validation_full
WHERE prediction_time_utc >= TIMESTAMPTZ '2025-09-01 00:00:00+00'
  AND prediction_time_utc < TIMESTAMPTZ '2025-09-02 00:00:00+00';
```

## Checks still required before training

- Finish the multi-year download and inspect unavailable/invalid source hours.
- Complete monthly features, split counts, and event-separation checks.
- Assess weather/label exclusion bias by split, borough and storm. Citywide
  uncertainty masks can preferentially exclude flood-heavy hours; report this
  selection effect rather than treating eligible rows as unbiased citywide truth.
- Inspect each split's positive prevalence and decide whether the training
  negative sample needs expansion; its current fraction is a practical starting
  configuration, not an optimized modeling choice.
- Review and commit code, review the final report, freeze data hashes, and obtain
  the user's instruction to start training.

Git operations are pending: the request to create
`codex/climate-baseline-preparation` was rejected by the user permission dialog.
The original `feature/climate-module` branch has not been switched, committed or
pushed by this run. New source files remain reviewable in the working tree.

Run tests with `python -m unittest discover -s tests -v`. Generated data remain
ignored by Git. Preserve the original target configuration supplied by the user
as part of the eventual source commit.
