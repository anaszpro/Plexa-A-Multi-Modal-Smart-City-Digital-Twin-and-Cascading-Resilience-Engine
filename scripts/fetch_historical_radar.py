"""
Fetch historical MRMS radar data from the IEM archives, process to Parquet, and delete raw files.

Usage:
    python scripts/fetch_historical_radar.py --start YYYY-MM-DD --end YYYY-MM-DD
"""
import os
import sys
import gzip
import shutil
import requests
import argparse
import xarray as xr
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from pathlib import Path

NYC_LAT = (40.49, 40.92)
NYC_LON = (-74.27, -73.68)
LON_MIN, LON_MAX = (lon % 360 for lon in NYC_LON)

def fetch_and_process(start_date, end_date):
    current_time = start_date
    out_dir = Path("data/raw/radar_parquet")
    out_dir.mkdir(parents=True, exist_ok=True)
    
    temp_dir = Path("data/raw/temp_radar")
    temp_dir.mkdir(parents=True, exist_ok=True)

    while current_time < end_date:
        ts_str = current_time.strftime('%Y%m%d_%H%M%S')
        out_file = out_dir / f"radar_{ts_str}.parquet"
        
        if out_file.exists():
            print(f"Skipping {ts_str} - already processed")
            current_time += timedelta(hours=1)
            continue
            
        yyyy = current_time.strftime('%Y')
        mm = current_time.strftime('%m')
        dd = current_time.strftime('%d')
        file_ts = current_time.strftime('%Y%m%d-%H0000')
        
        # Check both the primary and historical deep-archive servers
        base_urls = [
            f"https://mtarchive.geol.iastate.edu/{yyyy}/{mm}/{dd}/mrms/ncep/MultiSensor_QPE_01H_Pass2",
            f"https://mesonet.agron.iastate.edu/archive/data/{yyyy}/{mm}/{dd}/mrms/ncep/MultiSensor_QPE_01H_Pass2"
        ]
        
        filename_gz = f"MultiSensor_QPE_01H_Pass2_00.00_{file_ts}.grib2.gz"
        gz_path = temp_dir / filename_gz
        grib_path = temp_dir / filename_gz.replace('.gz', '')
        
        success = False
        last_url_tried = ""
        
        for base_url in base_urls:
            url = f"{base_url}/{filename_gz}"
            last_url_tried = url
            try:
                resp = requests.get(url, stream=True, timeout=30)
                if resp.status_code == 200:
                    with open(gz_path, 'wb') as f:
                        shutil.copyfileobj(resp.raw, f)
                    success = True
                    break
            except requests.exceptions.RequestException:
                pass
                
        if not success:
            print(f"Missing data for {ts_str}. Last URL checked: {last_url_tried}")
            current_time += timedelta(hours=1)
            continue
            
        try:
            # Decompress
            with gzip.open(gz_path, 'rb') as f_in:
                with open(grib_path, 'wb') as f_out:
                    shutil.copyfileobj(f_in, f_out)
                    
            # Process
            ds = xr.open_dataset(grib_path, engine="cfgrib", backend_kwargs={"indexpath": ""})
            rain = ds[list(ds.data_vars)[0]].sortby("latitude")
            cropped = rain.sel(latitude=slice(*NYC_LAT), longitude=slice(LON_MIN, LON_MAX)).load()
            ds.close()
            
            values = cropped.values.ravel().astype(float)
            df = pd.DataFrame({
                "ts_utc": current_time,
                "cell_id": np.arange(len(values)),
                "rain_mm": values
            })
            
            df = df[df["rain_mm"] > 0]
            df.to_parquet(out_file, index=False)
            print(f"Processed {ts_str} -> {len(df)} raining cells")
            
        except Exception as e:
            print(f"FAILED {ts_str} processing: {e}")
        finally:
            if gz_path.exists(): gz_path.unlink()
            if grib_path.exists(): grib_path.unlink()
            
        current_time += timedelta(hours=1)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True, help="YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="YYYY-MM-DD (exclusive)")
    args = parser.parse_args()
    
    start = datetime.strptime(args.start, "%Y-%m-%d")
    end = datetime.strptime(args.end, "%Y-%m-%d")
    fetch_and_process(start, end)
