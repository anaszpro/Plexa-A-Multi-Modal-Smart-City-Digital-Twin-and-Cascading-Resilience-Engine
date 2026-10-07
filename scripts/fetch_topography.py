"""
Fetch Digital Elevation Model (DEM) data for NYC, calculate elevation and slope 
for each street segment, and save as a Parquet feature table.

Usage:
    python scripts/fetch_topography.py
"""
import os
import requests
import rasterio
import pandas as pd
import geopandas as gpd
import numpy as np
from rasterio.mask import mask
from dotenv import load_dotenv
import warnings

# Load the API key from the .env file automatically
load_dotenv()
warnings.filterwarnings("ignore")

def download_nyc_dem(tif_path):
    # Retrieve API key from environment
    api_key = os.environ.get("OPENTOPOGRAPHY_API_KEY")
    if not api_key or api_key == "YOUR_API_KEY_HERE":
        raise ValueError("Please set your OPENTOPOGRAPHY_API_KEY environment variable.")

    # NYC Bounding Box
    minx, miny, maxx, maxy = -74.27, 40.49, -73.68, 40.92
    
    # Request 30m SRTM DEM from OpenTopography API
    url = f"https://portal.opentopography.org/API/globaldem?demtype=SRTMGL1&south={miny}&north={maxy}&west={minx}&east={maxx}&outputFormat=GTiff&API_Key={api_key}"
    
    print("Downloading NYC Elevation Data (DEM) from OpenTopography...")
    response = requests.get(url, stream=True)
    if response.status_code == 200:
        with open(tif_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        print("Download complete.")
    else:
        raise Exception(f"Failed to download DEM. Status code: {response.status_code}\nResponse: {response.text}")

def calculate_street_topography():
    os.makedirs("data/raw", exist_ok=True)
    os.makedirs("data/processed", exist_ok=True)
    
    dem_path = "data/raw/nyc_dem.tif"
    out_path = "data/processed/street_topography.parquet"
    
    if not os.path.exists(dem_path):
        download_nyc_dem(dem_path)
    else:
        print("DEM already downloaded.")
        
    print("Loading NYC street network...")
    streets = gpd.read_parquet("data/raw/nyc_streets.parquet")
    
    print("Extracting elevation and slope for 139,000 streets (this takes a minute)...")
    results = []
    
    with rasterio.open(dem_path) as src:
        # Reproject streets to match DEM CRS
        streets = streets.to_crs(src.crs)
        
        for idx, row in streets.iterrows():
            geom = row.geometry
            try:
                # Mask the raster with the street segment geometry
                out_image, _ = mask(src, [geom], crop=True)
                
                # Filter out no-data values
                valid_pixels = out_image[out_image > -9000]
                
                if len(valid_pixels) > 0:
                    mean_elev = np.mean(valid_pixels)
                    # Length in degrees converted roughly to meters for slope calculation
                    length_m = geom.length * 111000 
                    if length_m > 0:
                        slope = (np.max(valid_pixels) - np.min(valid_pixels)) / length_m
                    else:
                        slope = 0
                else:
                    mean_elev = 0
                    slope = 0
                    
            except ValueError:
                # Geometry doesn't overlap raster perfectly
                mean_elev = 0
                slope = 0
                
            results.append({
                'osmid': row['osmid'],
                'u': row['u'],
                'v': row['v'],
                'key': row['key'],
                'elevation_m': float(mean_elev),
                'slope': float(slope)
            })

    print("Saving topography features...")
    topo_df = pd.DataFrame(results)
    topo_df.to_parquet(out_path, index=False)
    print(f"Done! Saved to {out_path}")

if __name__ == "__main__":
    calculate_street_topography()
