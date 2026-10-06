-- Plexa Climate module: database schema
-- Three layers, each in its own Postgres schema.
-- All timestamps are stored in UTC.

CREATE SCHEMA IF NOT EXISTS raw;
CREATE SCHEMA IF NOT EXISTS clean;
CREATE SCHEMA IF NOT EXISTS features;

-- RAW: data as ingested, nothing fixed or filled in

-- One row per radar hour per borough, aggregated from the MRMS grid at ingest time.
CREATE TABLE IF NOT EXISTS raw.radar_hourly_borough (
    ts_utc       TIMESTAMPTZ      NOT NULL,
    borough      TEXT             NOT NULL,
    mean_mm      DOUBLE PRECISION,
    max_mm       DOUBLE PRECISION,
    n_cells      INTEGER,
    source_file  TEXT,
    ingested_at  TIMESTAMPTZ      NOT NULL DEFAULT now(),
    PRIMARY KEY (ts_utc, borough)
);

-- One row per 311 complaint, as returned by NYC Open Data.
CREATE TABLE IF NOT EXISTS raw.complaints_311 (
    unique_key      BIGINT       PRIMARY KEY,
    created_at      TIMESTAMPTZ  NOT NULL,
    complaint_type  TEXT,
    borough         TEXT,
    latitude        DOUBLE PRECISION,
    longitude       DOUBLE PRECISION,
    ingested_at     TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_complaints_created ON raw.complaints_311 (created_at);

-- CLEAN: validated, one row per hour per borough, gaps made explicit

CREATE TABLE IF NOT EXISTS clean.rainfall_hourly (
    ts_utc      TIMESTAMPTZ  NOT NULL,
    borough     TEXT         NOT NULL,
    mean_mm     DOUBLE PRECISION,
    max_mm      DOUBLE PRECISION,
    is_missing  BOOLEAN      NOT NULL DEFAULT FALSE,
    PRIMARY KEY (ts_utc, borough)
);

CREATE TABLE IF NOT EXISTS clean.complaints_hourly (
    ts_utc        TIMESTAMPTZ  NOT NULL,
    borough       TEXT         NOT NULL,
    sewer_count   INTEGER      NOT NULL DEFAULT 0,
    PRIMARY KEY (ts_utc, borough)
);

-- FEATURES: what the model trains on

CREATE TABLE IF NOT EXISTS features.flood_features (
    ts_utc       TIMESTAMPTZ  NOT NULL,
    borough      TEXT         NOT NULL,
    rain_1h_mm   DOUBLE PRECISION,
    rain_3h_mm   DOUBLE PRECISION,
    rain_24h_mm  DOUBLE PRECISION,
    rain_48h_mm  DOUBLE PRECISION,
    flood_label  SMALLINT,   -- NULL until the labeling rule is settled
    PRIMARY KEY (ts_utc, borough)
);
