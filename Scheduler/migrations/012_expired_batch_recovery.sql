-- An expired batch lease is fenced even when its worker cannot acknowledge
-- cleanup. Keep interactive assignments gated on their existing cleanup ack.
DO $$
DECLARE old_constraint RECORD;
BEGIN
 IF NOT EXISTS (
  SELECT 1 FROM pg_constraint
  WHERE conrelid = 'worker_assignments'::regclass
    AND conname = 'ck_assignment_release_or_expired_batch'
 ) THEN
  FOR old_constraint IN
   SELECT conname FROM pg_constraint
   WHERE conrelid = 'worker_assignments'::regclass
     AND contype = 'c'
     AND position('released_at' in pg_get_constraintdef(oid)) > 0
  LOOP
   EXECUTE 'ALTER TABLE worker_assignments DROP CONSTRAINT ' || quote_ident(old_constraint.conname);
  END LOOP;
  ALTER TABLE worker_assignments
   ADD CONSTRAINT ck_assignment_release_or_expired_batch
   CHECK (
    (released_at IS NULL AND state != 'RELEASED') OR
    (released_at IS NOT NULL AND state = 'RELEASED' AND (cleanup_ack OR job_id IS NOT NULL))
   );
 END IF;
END $$;
