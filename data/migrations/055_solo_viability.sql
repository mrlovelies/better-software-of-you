-- Solo viability scoring dimensions for triage
ALTER TABLE harvest_signals ADD COLUMN subreddit_subscribers INTEGER;
ALTER TABLE harvest_triage ADD COLUMN solo_viability_score INTEGER;
ALTER TABLE harvest_triage ADD COLUMN automation_potential_score INTEGER;
ALTER TABLE harvest_triage ADD COLUMN ops_burden_score INTEGER;
ALTER TABLE harvest_triage ADD COLUMN subreddit_confidence TEXT;
