-- Add user_impact column to infra_recommendations.
-- This is the field that explicitly answers "what does this do for me" —
-- the missing piece for actionable recommendations. Tier 2 must populate
-- it; recs without a concrete user_impact get demoted in post-validation.

ALTER TABLE infra_recommendations ADD COLUMN user_impact TEXT;
