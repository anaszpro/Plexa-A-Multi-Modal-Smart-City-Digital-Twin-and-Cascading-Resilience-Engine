"""
Extract NYC drivable street network using OSMnx.
Saves the street edges as a static Parquet file for the data warehouse.

Usage:
    python scripts/build_street_graph.py
"""
import os
import osmnx as ox
import geopandas as gpd

def main():
    print("Downloading NYC road network from OpenStreetMap...")
    G = ox.graph_from_place("New York City, New York, USA", network_type="drive")
    
    print("Converting graph to street edges...")
    _, edges = ox.graph_to_gdfs(G)
    
    edges = edges.reset_index()
    
    keep_cols = ['u', 'v', 'key', 'osmid', 'name', 'length', 'geometry']
    edges = edges[[c for c in keep_cols if c in edges.columns]]
    
    # FIX: Parquet strictly enforces data types. 
    # We cast mixed columns (lists/ints/strings) to strings to prevent PyArrow crashes.
    for col in ['osmid', 'name']:
        if col in edges.columns:
            edges[col] = edges[col].astype(str)
    
    edges = edges.to_crs("EPSG:4326")
    
    print(f"Extracted {len(edges)} street segments.")
    
    os.makedirs("data/raw", exist_ok=True)
    out_path = "data/raw/nyc_streets.parquet"
    edges.to_parquet(out_path, index=False)
    print(f"Saved street network to {out_path}")

if __name__ == "__main__":
    main()
