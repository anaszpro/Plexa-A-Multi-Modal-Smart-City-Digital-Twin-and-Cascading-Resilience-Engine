"""
Map NYC OSMnx street segments to the nearest MRMS radar grid cell.
Produces a static lookup table so we don't do spatial joins during hourly processing.

Usage:
    python scripts/map_streets_to_radar.py
"""
import os
import warnings
import xarray as xr
import pandas as pd
import geopandas as gpd
import numpy as np
from pathlib import Path

warnings.filterwarnings("ignore", category=UserWarning)

NYC_LAT = (40.49, 40.92)
NYC_LON = (-74.27, -73.68)
LON_MIN, LON_MAX = (lon % 360 for lon in NYC_LON)

def main():
    print("Loading sample radar file to extract grid...")
    radar_dir = Path("data/raw/radar_ida")
    sample_path = sorted(radar_dir.glob("*.grib2"))[0]
    
    ds = xr.open_dataset(sample_path, engine="cfgrib", backend_kwargs={"indexpath": ""})
    rain = ds[list(ds.data_vars)[0]].sortby("latitude")
    cropped = rain.sel(
        latitude=slice(*NYC_LAT),
        longitude=slice(LON_MIN, LON_MAX),
    ).load()
    ds.close()

    lats = cropped.latitude.values
    lons = cropped.longitude.values - 360
    lon_grid, lat_grid = np.meshgrid(lons, lats)
    
    radar_cells = gpd.GeoDataFrame(
        {"cell_id": np.arange(lat_grid.size)},
        geometry=gpd.points_from_xy(lon_grid.ravel(), lat_grid.ravel()),
        crs="EPSG:4326"
    )
    
    print("Loading street network...")
    streets = gpd.read_parquet("data/raw/nyc_streets.parquet")
    
    print("Calculating street centroids...")
    street_centroids = streets.copy()
    street_centroids["geometry"] = street_centroids.geometry.centroid
    
    print("Mapping streets to nearest radar cell...")
    mapping = gpd.sjoin_nearest(street_centroids, radar_cells, how="inner", distance_col="dist_to_cell")
    
    keep_cols = ['u', 'v', 'key', 'osmid', 'name', 'cell_id']
    final_mapping = mapping[[c for c in keep_cols if c in mapping.columns]]
    
    out_path = "data/processed/street_to_radar_cell.parquet"
    os.makedirs("data/processed", exist_ok=True)
    final_mapping.to_parquet(out_path, index=False)
    print(f"Saved {len(final_mapping)} street-to-cell mappings to {out_path}")

if __name__ == "__main__":
    main()
