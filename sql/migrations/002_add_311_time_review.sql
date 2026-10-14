-- Preserve complaints whose local timestamps cannot be resolved safely.
-- These records must not become hourly training labels without resolution.

BEGIN;

CREATE TABLE IF NOT EXISTS raw.complaints_311_time_review (
    unique_key        BIGINT PRIMARY KEY,
    created_date_local TEXT NOT NULL,
    time_issue        TEXT NOT NULL
                      CHECK (time_issue IN (
                          'ambiguous_local_time',
                          'nonexistent_local_time'
                      )),
    source_dataset    TEXT NOT NULL,
    source_record     JSONB NOT NULL,
    first_seen_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE raw.complaints_311_time_review IS
    'Source complaints with unresolved local timestamps. Preserve original '
    'records without inventing UTC times. Exclude from hourly labels until '
    'resolved and account for their uncertainty when constructing negatives.';

COMMENT ON COLUMN raw.complaints_311_time_review.source_record IS
    'Original record returned by the ingestion query, including its local '
    'timestamp and complaint descriptor.';

COMMIT;
