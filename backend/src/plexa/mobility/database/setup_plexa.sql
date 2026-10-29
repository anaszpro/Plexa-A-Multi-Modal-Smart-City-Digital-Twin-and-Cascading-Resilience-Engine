CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS timescaledb;

DROP TABLE IF EXISTS hourly_dst_flagged;
DROP TABLE IF EXISTS hourly;
DROP TABLE IF EXISTS segments;

-- One row per segment (4,488 expected)
CREATE TABLE segments (
    segment_id            BIGINT PRIMARY KEY,
    borough               TEXT,
    borough_source        TEXT,
    boro_disagreement     BOOLEAN NOT NULL DEFAULT FALSE,
    lion_boro             TEXT,
    automated_boro        TEXT,
    lion_key_used         TEXT NOT NULL,
    attr_source           TEXT,
    in_automated          BOOLEAN NOT NULL,
    in_historical         BOOLEAN NOT NULL,
    has_minor_series      BOOLEAN NOT NULL DEFAULT FALSE,
    hist_series           TEXT,
    hist_rows             INTEGER,
    conflict_rows         INTEGER,
    conflict_share        DOUBLE PRECISION,
    identity_conflict_all BOOLEAN,
    first_hour            TIMESTAMP,
    last_hour             TIMESTAMP,
    posted_speed          DOUBLE PRECISION,
    number_travel_lanes   DOUBLE PRECISION,
    number_park_lanes     DOUBLE PRECISION,
    number_total_lanes    DOUBLE PRECISION,
    streetwidth_min       DOUBLE PRECISION,
    streetwidth_max       DOUBLE PRECISION,
    shape_length          DOUBLE PRECISION,
    trafdir               TEXT,
    truck_route_type      DOUBLE PRECISION,
    snow_priority         TEXT,
    bikelane              DOUBLE PRECISION,
    lboro                 DOUBLE PRECISION,
    rboro                 DOUBLE PRECISION,
    lon                   DOUBLE PRECISION,
    lat                   DOUBLE PRECISION,
    lion_wkt_2263         TEXT,
    lion_geom             geometry(MultiLineString, 2263),
    geom_4326             geometry(Point, 4326)
);

-- Hourly fact table, UTC, unambiguous hours only (about 1.43M rows expected, minus 658 DST)
CREATE TABLE hourly (
    segment_id        BIGINT NOT NULL REFERENCES segments(segment_id),
    direction         TEXT NOT NULL,
    road_type         TEXT NOT NULL,
    hour_start_utc    TIMESTAMPTZ NOT NULL,
    volume            DOUBLE PRECISION NOT NULL,
    source            TEXT NOT NULL,
    study_id          BIGINT,
    orig_interval_min SMALLINT,
    identity_conflict BOOLEAN NOT NULL,
    partial_day       BOOLEAN,
    covid             BOOLEAN,
    sparse_year       BOOLEAN,
    baseline_ok       BOOLEAN NOT NULL,
    PRIMARY KEY (segment_id, direction, road_type, hour_start_utc)
);
SELECT create_hypertable('hourly', 'hour_start_utc',
                         chunk_time_interval => INTERVAL '1 month', if_not_exists => TRUE);
CREATE INDEX hourly_baseline_idx ON hourly (segment_id, direction, road_type, hour_start_utc)
    WHERE baseline_ok;

-- DST-ambiguous hours, kept in local clock time (658 expected)
CREATE TABLE hourly_dst_flagged (
    segment_id        BIGINT NOT NULL REFERENCES segments(segment_id),
    direction         TEXT NOT NULL,
    road_type         TEXT NOT NULL,
    hour_start_local  TIMESTAMP NOT NULL,
    volume            DOUBLE PRECISION NOT NULL,
    source            TEXT NOT NULL,
    study_id          BIGINT,
    orig_interval_min SMALLINT,
    identity_conflict BOOLEAN NOT NULL,
    PRIMARY KEY (segment_id, direction, road_type, hour_start_local)
);