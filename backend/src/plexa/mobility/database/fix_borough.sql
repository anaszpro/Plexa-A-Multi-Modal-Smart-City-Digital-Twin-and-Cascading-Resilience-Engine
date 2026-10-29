BEGIN;
UPDATE segments SET borough = 'Manhattan', borough_source = 'reviewed' WHERE segment_id = 155079;
UPDATE segments SET borough = 'Brooklyn',  borough_source = 'reviewed' WHERE segment_id IN (158339, 66420);
UPDATE segments SET borough_source = 'automated_unresolved' WHERE segment_id = 136594;
COMMIT;
SELECT segment_id, borough, borough_source, boro_disagreement
  FROM segments WHERE boro_disagreement ORDER BY segment_id;