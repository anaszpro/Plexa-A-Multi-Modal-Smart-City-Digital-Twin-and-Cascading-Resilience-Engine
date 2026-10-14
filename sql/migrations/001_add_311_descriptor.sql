-- Preserve the source complaint description for later label classification.
-- Existing rows remain unchanged; their descriptors will initially be NULL.
-- Re-running this migration is safe.

BEGIN;

ALTER TABLE raw.complaints_311
    ADD COLUMN IF NOT EXISTS descriptor TEXT;

COMMENT ON COLUMN raw.complaints_311.descriptor IS
    'NYC 311 source descriptor. NULL means unavailable or not yet collected; '
    'this field does not itself establish whether flooding occurred.';

COMMIT;
