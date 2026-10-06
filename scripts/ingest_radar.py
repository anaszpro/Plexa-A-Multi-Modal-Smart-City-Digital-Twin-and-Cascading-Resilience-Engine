"""Load hourly MRMS radar rainfall into Postgres, aggregated to borough level.

Usage:
    python scripts/ingest_radar.py data/raw/radar_ida

For each .grib2 file: crop to the NYC bounding box, assign grid cells to
boroughs, and upsert one row per borough into raw.radar_hourly_borough.
Files already loaded (matched by source_file) are skipped, so the script is
safe to re-run.
"""
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import psycopg2
import xarray as xr
from psycopg2.extras import execute_values

NYC_LAT = (40.49, 40.92)
NYC_LON = (-74.27, -73.68)
LON_MIN, LON_MAX = (lon % 360 for lon in NYC_LON)

BOROUGHS_PATH = Path("data/raw/nyc_boroughs.geojson")
FILENAME_TS = re.compile(r"(\d{8})-(\d{6})\.grib2$")

UPSERT_SQL = """
    INSERT INTO raw.radar_hourly_borough
        (ts_utc, borough, mean_mm, max_mm, n_cells, source_file)
    VALUES %s
    ON CONFLICT (ts_utc, borough) DO UPDATE SET
        mean_mm = EXCLUDED.mean_mm,
        max_mm = EXCLUDED.max_mm,
        n_cells = EXCLUDED.n_cells,
        source_file = EXCLUDED.source_file,
        ingested_at = now()
"""


def timestamp_from_name(path):
    """Hour the file covers, in UTC. The hour is the end of the accumulation."""
    m = FILENAME_TS.search(path.name)
    if not m:
        raise ValueError(f"No timestamp in filename: {path.name}")
    return datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S").replace(
        tzinfo=timezone.utc
    )


def open_cropped(path):
    ds = xr.open_dataset(path, engine="cfgrib", backend_kwargs={"indexpath": ""})
    rain = ds[list(ds.data_vars)[0]].sortby("latitude")
    cropped = rain.sel(
        latitude=slice(*NYC_LAT),
        longitude=slice(LON_MIN, LON_MAX),
    ).load()
    ds.close()
    return cropped


def build_cell_map(cropped, boroughs):
    """Return (flat cell indices inside NYC, borough name for each)."""
    lats = cropped.latitude.values
    lons = cropped.longitude.values - 360
    lon_grid, lat_grid = np.meshgrid(lons, lats)
    points = gpd.GeoDataFrame(
        {"cell": np.arange(lat_grid.size)},
        geometry=gpd.points_from_xy(lon_grid.ravel(), lat_grid.ravel()),
        crs="EPSG:4326",
    )
    joined = gpd.sjoin(
        points, boroughs[["boroname", "geometry"]], how="inner", predicate="within"
    ).drop_duplicates("cell")
    return joined["cell"].to_numpy(), joined["boroname"].to_numpy()


def aggregate(cropped, cells, names):
    values = cropped.values.ravel()[cells].astype(float)
    values[values < 0] = np.nan  # negative values are radar flags, not rainfall
    df = pd.DataFrame({"borough": names, "mm": values})
    return df.groupby("borough")["mm"].agg(
        mean_mm="mean", max_mm="max", n_cells="count"
    )


def none_if_nan(x):
    return None if pd.isna(x) else float(x)


def main(radar_dir):
    files = sorted(Path(radar_dir).glob("*.grib2"))
    if not files:
        sys.exit(f"No .grib2 files found in {radar_dir}")

    boroughs = gpd.read_file(BOROUGHS_PATH)
    conn = psycopg2.connect(dbname=os.environ.get("PLEXA_DB", "plexa_climate"))

    with conn, conn.cursor() as cur:
        cur.execute("SELECT DISTINCT source_file FROM raw.radar_hourly_borough")
        done = {r[0] for r in cur.fetchall()}

    todo = [f for f in files if f.name not in done]
    print(f"{len(files)} files found, {len(files) - len(todo)} already loaded, {len(todo)} to do")

    cells = names = grid_shape = None
    loaded = failed = 0

    for path in todo:
        try:
            cropped = open_cropped(path)
            if cells is None:
                cells, names = build_cell_map(cropped, boroughs)
                grid_shape = cropped.shape
                print(f"Grid {grid_shape}, {len(cells)} cells inside NYC boroughs")
            elif cropped.shape != grid_shape:
                raise ValueError(f"Grid shape {cropped.shape} differs from {grid_shape}")

            stats = aggregate(cropped, cells, names)
            ts = timestamp_from_name(path)
            rows = [
                (ts, b, none_if_nan(r.mean_mm), none_if_nan(r.max_mm), int(r.n_cells), path.name)
                for b, r in stats.iterrows()
            ]
            with conn, conn.cursor() as cur:
                execute_values(cur, UPSERT_SQL, rows)
            loaded += 1
        except Exception as e:
            failed += 1
            print(f"FAILED {path.name}: {e}")

    conn.close()
    print(f"Done. Loaded {loaded}, failed {failed}.")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("Usage: python scripts/ingest_radar.py <radar_dir>")
    main(sys.argv[1])
