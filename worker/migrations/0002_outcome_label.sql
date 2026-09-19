-- Outcome labels for accurate side names ("Win for Chelsea").
-- D1 has no IF NOT EXISTS for ADD COLUMN; this migration runs once.
ALTER TABLE selections ADD COLUMN outcome_label TEXT;
