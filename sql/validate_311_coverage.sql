-- Reconcile stored complaints for the selected baseline snapshot.
-- New York local dates: 2020-10-14 inclusive to 2026-10-07 exclusive.
-- Counts alone do not establish physical flood coverage.

BEGIN READ ONLY;

-- Annual counts, including records whose UTC time remains unresolved.
WITH combined AS (
    SELECT
        created_at AT TIME ZONE 'America/New_York' AS local_time,
        'normal' AS record_group
    FROM raw.complaints_311

    UNION ALL

    SELECT
        created_date_local::timestamp AS local_time,
        'time_review' AS record_group
    FROM raw.complaints_311_time_review
),
selected AS (
    SELECT
        extract(year FROM local_time)::integer AS year,
        record_group
    FROM combined
    WHERE local_time >= TIMESTAMP '2020-10-14'
      AND local_time <  TIMESTAMP '2026-10-07'
)
SELECT
    COALESCE(year::text, 'TOTAL') AS year,
    count(*) FILTER (
        WHERE record_group = 'normal'
    ) AS normal_records,
    count(*) FILTER (
        WHERE record_group = 'time_review'
    ) AS review_records,
    count(*) AS combined_records
FROM selected
GROUP BY ROLLUP(year)
ORDER BY year NULLS LAST;

-- A complaint must not exist in both storage groups.
SELECT count(*) AS ids_in_both_tables
FROM raw.complaints_311 AS normal
JOIN raw.complaints_311_time_review AS review
    USING (unique_key);

-- Inspect the available categories and descriptors before defining labels.
WITH combined AS (
    SELECT
        created_at AT TIME ZONE 'America/New_York' AS local_time,
        complaint_type,
        descriptor,
        'normal' AS record_group
    FROM raw.complaints_311

    UNION ALL

    SELECT
        created_date_local::timestamp,
        source_record->>'complaint_type',
        source_record->>'descriptor',
        'time_review'
    FROM raw.complaints_311_time_review
)
SELECT
    complaint_type,
    descriptor,
    count(*) FILTER (
        WHERE record_group = 'normal'
    ) AS normal_records,
    count(*) FILTER (
        WHERE record_group = 'time_review'
    ) AS review_records
FROM combined
WHERE local_time >= TIMESTAMP '2020-10-14'
  AND local_time <  TIMESTAMP '2026-10-07'
GROUP BY complaint_type, descriptor
ORDER BY complaint_type, normal_records DESC, descriptor;

COMMIT;
