"""
Extract hourly MRMS radar rainfall for all 837 NYC grid cells.
Saves output as Parquet files for fast analytical queries via DuckDB.

Usage:
    python scripts/ingest_radar_parquet.py data/raw/radar_ida
"""
import os
import sys
import re
from datetime import datetime, timezone
from pathlib import Path
import xarray as xr
import pandas as pd
import numpy as np

NYC_LAT = (40.49, 40.92)
NYC_LON = (-74.27, -73.68)
LON_MIN, LON_MAX = (lon % 360 for lon in NYC_LON)

FILENAME_TS = re.compile(r"(\d{8})-(\d{6})\.grib2$")

def timestamp_from_name(path):
    m = FILENAME_TS.search(path.name)
    if not m:
        raise ValueError(f"No timestamp in filename: {path.name}")
    return datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)

def main(radar_dir):
    files = sorted(Path(radar_dir).glob("*.grib2"))
    if not files:
        sys.exit(f"No .grib2 files found in {radar_dir}")

    out_dir = Path("data/raw/radar_parquet")
    out_dir.mkdir(parents=True, exist_ok=True)

    loaded = 0
    for path in files:
        ts = timestamp_from_name(path)
        out_file = out_dir / f"radar_{ts.strftime('%Y%m%d_%H%M%S')}.parquet"
        
        # Skip if already processed
        if out_file.exists():
            continue

        try:
            ds = xr.open_dataset(path, engine="cfgrib", backend_kwargs={"indexpath": ""})
            rain = ds[list(ds.data_vars)[0]].sortby("latitude")
            cropped = rain.sel(
                latitude=slice(*NYC_LAT),
                longitude=slice(LON_MIN, LON_MAX),
            ).load()
            ds.close()

            # Flatten the grid exactly as we did in the spatial mapping script
            values = cropped.values.ravel().astype(float)
            
            # The cell_id perfectly matches the index of the flattened array
            df = pd.DataFrame({
                "ts_utc": ts,
                "cell_id": np.arange(len(values)),
                "rain_mm": values
            })
            
            # Drop negative values (radar error flags) and zeroes to save massive disk space
            df = df[df["rain_mm"] > 0]
            
            df.to_parquet(out_file, index=False)
            loaded += 1
            print(f"Processed {path.name} -> {len(df)} raining cells")
            
        except Exception as e:
            print(f"FAILED {path.name}: {e}")

    print(f"Done. Generated {loaded} new hourly parquet files.")

if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("Usage: python scripts/ingest_radar_parquet.py <radar_dir>")
    main(sys.argv[1])
