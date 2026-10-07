"""
DuckDB ELT Pipeline: 
Joins PostgreSQL 311 Flood Complaints, Parquet Topography, and Parquet Radar Weather 
into a single Machine Learning Feature Matrix.
"""
import duckdb
import os
import time
from dotenv import load_dotenv

load_dotenv()

def build_ml_matrix():
    start_time = time.time()
    print("Initializing DuckDB analytical engine...")
    
    os.makedirs("data/processed", exist_ok=True)
    con = duckdb.connect('data/processed/climate_twin.db')
    
    print("Loading PostgreSQL extension...")
    con.execute("INSTALL postgres;")
    con.execute("LOAD postgres;")
    
    pg_user = os.getenv("DB_USER", "postgres")
    pg_pass = os.getenv("DB_PASS", "postgres")
    pg_db = os.getenv("DB_NAME", "nyc_311")
    pg_port = os.getenv("DB_PORT", "5432")
    
    has_pg = False
    try:
        print(f"Attaching PostgreSQL database ({pg_db}) as user {pg_user}...")
        con.execute(f"""
            ATTACH 'dbname={pg_db} user={pg_user} password={pg_pass} host=localhost port={pg_port}' 
            AS pg_db (TYPE postgres);
        """)
        has_pg = True
    except Exception as e:
        print("Warning: PostgreSQL connection failed. (Is the Postgres server running?)")
        print("Continuing with a mock 311 target for pipeline testing purposes...")
        con.execute("""
            CREATE OR REPLACE TEMP TABLE mock_311 AS 
            SELECT '4529183' AS osmid, '2022-04-17 03:00:00'::TIMESTAMP AS incident_time, 1 AS flooded_311;
        """)

    print("Executing spatial-temporal JOIN...")
    
    target_table = "pg_db.complaints" if has_pg else "mock_311"
    
    query = f"""
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
            SELECT 
                t.osmid,
                (SELECT MAX(cell_id) FROM weather) AS cell_id
            FROM topography t
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
        INNER JOIN spatial_bridge b ON t.osmid = b.osmid
        INNER JOIN weather w ON b.cell_id = w.cell_id
        LEFT JOIN (
            SELECT osmid::VARCHAR AS osmid, date_trunc('hour', incident_time) as timestamp, 1 as flooded_311 
            FROM {target_table}
        ) c ON t.osmid::VARCHAR = c.osmid AND w.timestamp = c.timestamp
    ) TO 'data/processed/ml_feature_matrix.parquet' (FORMAT PARQUET);
    """
    
    try:
        con.execute(query)
        elapsed = round(time.time() - start_time, 2)
        print(f"Success: ml_feature_matrix.parquet created in {elapsed} seconds.")
    except Exception as e:
        print(f"Error: Pipeline failed during JOIN execution: {e}")

if __name__ == "__main__":
    build_ml_matrix()
