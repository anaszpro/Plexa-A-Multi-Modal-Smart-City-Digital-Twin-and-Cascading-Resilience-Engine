"""
Map NYC 311 sewer complaints from PostgreSQL to the nearest OSMnx street segment.
Creates the target labels for the machine learning model.

Usage:
    python scripts/map_311_to_streets.py
"""
import os
import pandas as pd
import geopandas as gpd
import psycopg2
import warnings

warnings.filterwarnings("ignore")

def main():
    print("1. Fetching 311 complaints from PostgreSQL...")
    conn = psycopg2.connect(dbname=os.environ.get("PLEXA_DB", "plexa_climate"))
    
    # Removed 'descriptor' from the SELECT statement. 
    # We only need the timestamp and coordinates for ML labels.
    query = """
        SELECT unique_key, created_at, latitude, longitude 
        FROM raw.complaints_311 
        WHERE latitude IS NOT NULL AND longitude IS NOT NULL
    """
    complaints = pd.read_sql(query, conn)
    conn.close()
    
    print(f"Loaded {len(complaints)} complaints. Converting to spatial points...")
    complaints_gdf = gpd.GeoDataFrame(
        complaints,
        geometry=gpd.points_from_xy(complaints.longitude, complaints.latitude),
        crs="EPSG:4326"
    )
    
    print("2. Loading NYC street network...")
    streets = gpd.read_parquet("data/raw/nyc_streets.parquet")
    keep_cols = ['u', 'v', 'key', 'osmid', 'name', 'geometry']
    streets_geom = streets[keep_cols]
    
    print("3. Projecting to NYC State Plane (feet) for accurate distance snapping...")
    complaints_gdf = complaints_gdf.to_crs("EPSG:2263")
    streets_geom = streets_geom.to_crs("EPSG:2263")
    
    print("4. Mapping complaints to the nearest street (max 100 feet)...")
    mapped = gpd.sjoin_nearest(
        complaints_gdf, 
        streets_geom, 
        how="inner", 
        max_distance=100, 
        distance_col="dist_to_street_ft"
    )
    
    print(f"Successfully snapped {len(mapped)} complaints to streets.")
    
    out_df = pd.DataFrame(mapped.drop(columns=['geometry']))
    os.makedirs("data/processed", exist_ok=True)
    out_path = "data/processed/street_311_labels.parquet"
    out_df.to_parquet(out_path, index=False)
    
    print(f"Saved ground truth ML labels to {out_path}")

if __name__ == "__main__":
    main()
