"""
Download and process historical MRMS hourly rainfall for NYC.

The output keeps every cell in the NYC bounding box:
- Positive values represent observed rainfall.
- Zero represents an observed dry cell.
- Missing or invalid values become NaN with is_missing=True.
- All timestamps use UTC.

Test using existing local files:
    python scripts/fetch_historical_radar.py \
        --local-dir data/raw/radar_ida \
        --output-dir data/processed/radar_validation

Download a historical period:
    python scripts/fetch_historical_radar.py \
        --start 2021-09-01 \
        --end 2021-09-03

The end date is exclusive. New downloads default to radar_parquet_v2,
keeping them separate from the previous rainfall archive.
"""

import argparse
import gzip
import hashlib
import re
import shutil
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests
import xarray as xr


ROOT = Path(__file__).resolve().parents[1]

NYC_LAT = (40.49, 40.92)
NYC_LON = (-74.27 % 360, -73.68 % 360)

PRODUCT = "MultiSensor_QPE_01H_Pass2"

NAME_PATTERN = re.compile(
    r"MultiSensor_QPE_01H_Pass2_00\.00_(\d{8}-\d{6})\.grib2$"
)

COLUMNS = [
    "ts_utc",
    "cell_id",
    "latitude",
    "longitude",
    "rain_mm",
    "is_missing",
    "grid_id",
]


def timestamp_from_name(path):
    """Read the UTC observation time from a radar filename."""
    match = NAME_PATTERN.fullmatch(path.name)

    if match is None:
        raise ValueError(f"Unexpected product filename: {path.name}")

    return datetime.strptime(
        match.group(1), "%Y%m%d-%H%M%S"
    ).replace(tzinfo=timezone.utc)


def load_crop(path):
    """Open a radar file, verify its time, and extract the NYC grid."""
    expected_time = timestamp_from_name(path)

    with xr.open_dataset(
        path,
        engine="cfgrib",
        backend_kwargs={"indexpath": ""},
    ) as dataset:
        if len(dataset.data_vars) != 1:
            raise ValueError(
                f"Expected one rainfall variable: {path.name}"
            )

        valid_time = pd.Timestamp(
            dataset.valid_time.values
        ).tz_localize("UTC")

        if valid_time != expected_time:
            raise ValueError(
                f"Filename/GRIB time mismatch: "
                f"{path.name}: {valid_time}"
            )

        variable = next(iter(dataset.data_vars))

        rain = (
            dataset[variable]
            .sortby("latitude")
            .sortby("longitude")
        )

        crop = rain.sel(
            latitude=slice(*NYC_LAT),
            longitude=slice(*NYC_LON),
        )

        crop = crop.transpose("latitude", "longitude").load()

    if not crop.size:
        raise ValueError(f"Empty NYC crop: {path.name}")

    return crop, expected_time


def make_frame(values, timestamp, lats, lons, grid_id):
    """Create one row per cell, preserving observed zero rainfall."""
    values = np.asarray(values, dtype=np.float64).ravel()

    missing = ~np.isfinite(values) | (values < 0)

    lon_grid, lat_grid = np.meshgrid(lons - 360, lats)

    return pd.DataFrame({
        "ts_utc": pd.DatetimeIndex(
            [timestamp] * values.size,
            tz="UTC",
        ),
        "cell_id": np.arange(values.size, dtype=np.int32),
        "latitude": lat_grid.ravel(),
        "longitude": lon_grid.ravel(),
        "rain_mm": np.where(missing, np.nan, values),
        "is_missing": missing,
        "grid_id": grid_id,
    })


def validate_frame(frame, timestamp, lats, lons, grid_id):
    """Check output structure, timestamps, coordinates, and rainfall."""
    expected_cells = len(lats) * len(lons)

    if list(frame.columns) != COLUMNS:
        raise ValueError(
            "Wrong output schema; legacy files cannot be skipped"
        )

    if len(frame) != expected_cells:
        raise ValueError("Output does not contain the complete grid")

    if str(frame.ts_utc.dt.tz) != "UTC":
        raise ValueError("Output timestamps must use UTC")

    if not frame.ts_utc.eq(timestamp).all():
        raise ValueError("Output timestamps do not match the requested hour")

    if not np.array_equal(
        frame.cell_id.to_numpy(),
        np.arange(expected_cells),
    ):
        raise ValueError("Cell IDs must be ordered, unique and complete")

    lon_grid, lat_grid = np.meshgrid(lons - 360, lats)

    if not np.array_equal(
        frame.latitude.to_numpy(),
        lat_grid.ravel(),
    ):
        raise ValueError("Latitude grid differs from reference")

    if not np.array_equal(
        frame.longitude.to_numpy(),
        lon_grid.ravel(),
    ):
        raise ValueError("Longitude grid differs from reference")

    if not frame.grid_id.eq(grid_id).all():
        raise ValueError("Grid fingerprint differs from reference")

    if frame.is_missing.dtype != bool:
        raise ValueError("is_missing must contain Boolean values")

    if not frame.rain_mm.isna().eq(frame.is_missing).all():
        raise ValueError("Missing flags and rainfall nulls must agree")

    valid_rain = frame.loc[~frame.is_missing, "rain_mm"]

    if not np.isfinite(valid_rain).all() or (valid_rain < 0).any():
        raise ValueError(
            "Observed rainfall must be finite and nonnegative"
        )


def atomic_write(frame, target):
    """Publish the output only after the Parquet write finishes."""
    with tempfile.NamedTemporaryFile(
        dir=target.parent,
        suffix=".parquet",
        delete=False,
    ) as temporary_file:
        temporary_path = Path(temporary_file.name)

    try:
        frame.to_parquet(temporary_path, index=False)
        temporary_path.replace(target)
    finally:
        temporary_path.unlink(missing_ok=True)


def download_hour(session, timestamp, directory):
    """Try both archive servers, retrying temporary failures."""
    name = (
        f"{PRODUCT}_00.00_"
        f"{timestamp:%Y%m%d-%H%M%S}.grib2"
    )

    target = directory / name

    archive_paths = [
        (
            "https://mtarchive.geol.iastate.edu/"
            f"{timestamp:%Y/%m/%d}/mrms/ncep/{PRODUCT}"
        ),
        (
            "https://mesonet.agron.iastate.edu/archive/data/"
            f"{timestamp:%Y/%m/%d}/mrms/ncep/{PRODUCT}"
        ),
    ]

    errors = []

    for archive in archive_paths:
        for attempt in range(3):
            try:
                with session.get(
                    f"{archive}/{name}.gz",
                    stream=True,
                    timeout=(15, 120),
                ) as response:
                    if response.status_code == 404:
                        errors.append(f"{archive}: 404")
                        break

                    response.raise_for_status()

                    with gzip.GzipFile(
                        fileobj=response.raw
                    ) as source, target.open("wb") as destination:
                        shutil.copyfileobj(source, destination)

                return target

            except (
                requests.RequestException,
                OSError,
                EOFError,
            ) as error:
                errors.append(
                    f"{archive}: {type(error).__name__}: {error}"
                )

                target.unlink(missing_ok=True)

                if attempt < 2:
                    time.sleep(2 ** attempt)

    raise RuntimeError("Download failed: " + " | ".join(errors))


def run(args):
    """Process local files or download the requested hourly period."""
    reference, _ = load_crop(args.reference_grid)

    lats = reference.latitude.values
    lons = reference.longitude.values

    coordinates = (
        np.concatenate([lats, lons])
        .astype("<f8")
        .tobytes()
    )

    grid_id = hashlib.sha256(coordinates).hexdigest()

    args.output_dir.mkdir(parents=True, exist_ok=True)

    if args.local_dir is not None:
        files = sorted(
            args.local_dir.glob(f"{PRODUCT}_*.grib2")
        )

        if not files:
            raise ValueError(
                f"No local GRIB2 files in {args.local_dir}"
            )

        jobs = [
            (timestamp_from_name(path), path)
            for path in files
        ]

    else:
        start = datetime.strptime(
            args.start, "%Y-%m-%d"
        ).replace(tzinfo=timezone.utc)

        end = datetime.strptime(
            args.end, "%Y-%m-%d"
        ).replace(tzinfo=timezone.utc)

        if start >= end:
            raise ValueError(
                "Start must be earlier than the exclusive end date"
            )

        total_hours = (end - start).days * 24

        jobs = [
            (start + timedelta(hours=hour), None)
            for hour in range(total_hours)
        ]

    written = 0
    skipped = 0
    failed = 0

    with requests.Session() as session:
        for timestamp, local_path in jobs:
            target = args.output_dir / (
                f"radar_{timestamp:%Y%m%d_%H%M%S}.parquet"
            )

            try:
                if target.exists() and not args.overwrite:
                    existing = pd.read_parquet(target)

                    validate_frame(
                        existing, timestamp, lats, lons, grid_id
                    )

                    skipped += 1
                    print(f"SKIP validated {target.name}")
                    continue

                with tempfile.TemporaryDirectory(
                    prefix="plexa-radar-"
                ) as temporary_directory:
                    source = local_path or download_hour(
                        session,
                        timestamp,
                        Path(temporary_directory),
                    )

                    crop, _ = load_crop(source)

                    same_latitudes = np.array_equal(
                        crop.latitude.values, lats
                    )

                    same_longitudes = np.array_equal(
                        crop.longitude.values, lons
                    )

                    if not same_latitudes or not same_longitudes:
                        raise ValueError(
                            "Source grid differs from reference; "
                            "cell IDs would change"
                        )

                    frame = make_frame(
                        crop.values,
                        timestamp,
                        lats,
                        lons,
                        grid_id,
                    )

                    validate_frame(
                        frame, timestamp, lats, lons, grid_id
                    )

                    atomic_write(frame, target)

                written += 1

                print(
                    f"WRITE {target.name}: "
                    f"cells={len(frame)}, "
                    f"dry={frame.rain_mm.eq(0).sum()}, "
                    f"missing={frame.is_missing.sum()}"
                )

            except Exception as error:
                failed += 1
                print(f"FAIL {timestamp.isoformat()}: {error}")

    print(
        f"Summary: written={written}, "
        f"validated_skips={skipped}, "
        f"failed={failed}"
    )

    return 1 if failed else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)

    parser.add_argument("--local-dir", type=Path)
    parser.add_argument("--start", help="UTC date, YYYY-MM-DD")
    parser.add_argument(
        "--end",
        help="Exclusive UTC date, YYYY-MM-DD",
    )

    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "data/raw/radar_parquet_v2",
    )

    parser.add_argument(
        "--reference-grid",
        type=Path,
        default=(
            ROOT
            / "data/raw/radar_ida"
            / "MultiSensor_QPE_01H_Pass2_00.00_20210902-000000.grib2"
        ),
    )

    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace existing outputs explicitly",
    )

    args = parser.parse_args()

    if args.local_dir is not None:
        if args.start or args.end:
            parser.error(
                "Use --local-dir OR --start/--end, not both"
            )
    elif not args.start or not args.end:
        parser.error(
            "Provide --local-dir or both --start and --end"
        )

    try:
        return run(args)
    except (ValueError, OSError) as error:
        parser.exit(1, f"Error: {error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
