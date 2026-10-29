BEGIN;
UPDATE hourly
   SET identity_conflict = FALSE,
       baseline_ok = (road_type = 'unspecified')
 WHERE segment_id = 29555 AND source = 'historical' AND identity_conflict;
UPDATE hourly_dst_flagged
   SET identity_conflict = FALSE
 WHERE segment_id = 29555 AND identity_conflict;
COMMIT;
SELECT count(*) AS identity_conflict_rows FROM hourly WHERE identity_conflict;
SELECT count(*) AS baseline_ok_rows FROM hourly WHERE baseline_ok;