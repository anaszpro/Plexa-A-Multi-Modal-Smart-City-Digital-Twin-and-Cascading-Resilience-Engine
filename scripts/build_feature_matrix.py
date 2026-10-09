"""
DuckDB ELT Pipeline: 
Joins Parquet Topography, Parquet Radar Weather, Parquet Spatial Bridge, 
and Parquet 311 Complaints into a single Machine Learning Feature Matrix.
"""
import duckdb
import os
import time

def build_ml_matrix():
    start_time = time.time()
    print("Initializing DuckDB analytical engine...")
    
    os.makedirs("data/processed", exist_ok=True)
    con = duckdb.connect('data/processed/climate_twin.db')

    print("Executing spatial-temporal JOIN using pure Parquet Lakehouse architecture...")
    
    query = """
    COPY (
        WITH weather AS (
            SELECT 
                ts_utc AS timestamp,
                cell_id,
                rain_mm,
                SUM(rain_mm) OVER (
                    PARTITION BY cell_id 
                    ORDER BY ts_utc 
                    ROWS BETWEEN 24 PRECEDING AND CURRENT ROW
                ) as rain_24hr_mm
            FROM read_parquet('data/raw/radar_parquet/*.parquet')
        ),
        topography AS (
            SELECT osmid, elevation_m, slope
            FROM read_parquet('data/processed/street_topography.parquet')
        ),
        spatial_bridge AS (
            SELECT osmid::VARCHAR AS osmid, cell_id
            FROM read_parquet('data/processed/street_to_radar_cell.parquet')
        ),
        complaints AS (
            SELECT 
                osmid::VARCHAR AS osmid, 
                date_trunc('hour', created_at) as timestamp, 
                1 as flooded_311 
            FROM read_parquet('data/processed/street_311_labels.parquet')
        )
        SELECT 
            t.osmid::VARCHAR AS osmid,
            w.timestamp,
            t.elevation_m,
            t.slope,
            w.rain_mm,
            w.rain_24hr_mm,
            COALESCE(c.flooded_311, 0) AS flooded_311
        FROM topography t
        INNER JOIN spatial_bridge b ON t.osmid::VARCHAR = b.osmid
        INNER JOIN weather w ON b.cell_id = w.cell_id
        LEFT JOIN complaints c ON t.osmid::VARCHAR = c.osmid AND w.timestamp = c.timestamp
    ) TO 'data/processed/ml_feature_matrix.parquet' (FORMAT PARQUET);
    """
    
    try:
        con.execute(query)
        elapsed = round(time.time() - start_time, 2)
        print(f"Success: Real ml_feature_matrix.parquet created in {elapsed} seconds.")
    except Exception as e:
        print(f"Error: Pipeline failed during JOIN execution: {e}")

if __name__ == "__main__":
    build_ml_matrix()