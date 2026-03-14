-- Writing Module — Computed views for draft navigation and project overviews.

-- Draft overview: each draft with its current version stats, feedback counts, and character list.
DROP VIEW IF EXISTS v_draft_overview;
CREATE VIEW v_draft_overview AS
SELECT
    d.id,
    d.project_id,
    d.parent_id,
    d.title,
    d.draft_type,
    d.sort_order,
    d.status,
    d.current_version,
    d.pov_character,
    d.synopsis,
    d.tags,
    d.word_count,
    d.created_at,
    d.updated_at,
    p.name AS project_name,
    parent.title AS parent_title,
    (SELECT COUNT(*) FROM draft_versions dv WHERE dv.draft_id = d.id) AS version_count,
    (SELECT COUNT(*) FROM draft_feedback df WHERE df.draft_id = d.id AND df.status = 'open') AS open_feedback,
    (SELECT COUNT(*) FROM draft_feedback df WHERE df.draft_id = d.id) AS total_feedback,
    (SELECT COUNT(*) FROM draft_lore_links dll WHERE dll.draft_id = d.id) AS lore_link_count,
    (SELECT GROUP_CONCAT(dc.character_name, ', ')
     FROM draft_characters dc WHERE dc.draft_id = d.id
     ORDER BY CASE dc.role WHEN 'pov' THEN 0 WHEN 'featured' THEN 1 WHEN 'mentioned' THEN 2 ELSE 3 END
    ) AS characters
FROM writing_drafts d
LEFT JOIN projects p ON p.id = d.project_id
LEFT JOIN writing_drafts parent ON parent.id = d.parent_id;

-- Project writing progress: per-project summary of all drafts.
DROP VIEW IF EXISTS v_writing_progress;
CREATE VIEW v_writing_progress AS
SELECT
    d.project_id,
    p.name AS project_name,
    COUNT(*) AS total_drafts,
    SUM(CASE WHEN d.status = 'final' THEN 1 ELSE 0 END) AS final_count,
    SUM(CASE WHEN d.status = 'draft' THEN 1 ELSE 0 END) AS draft_count,
    SUM(CASE WHEN d.status = 'revision' THEN 1 ELSE 0 END) AS revision_count,
    SUM(CASE WHEN d.status = 'outline' THEN 1 ELSE 0 END) AS outline_count,
    SUM(CASE WHEN d.status = 'review' THEN 1 ELSE 0 END) AS review_count,
    SUM(d.word_count) AS total_words,
    SUM(CASE WHEN d.status = 'final' THEN d.word_count ELSE 0 END) AS final_words,
    (SELECT COUNT(*) FROM draft_feedback df
     JOIN writing_drafts wd ON wd.id = df.draft_id
     WHERE wd.project_id = d.project_id AND df.status = 'open') AS open_feedback,
    GROUP_CONCAT(DISTINCT d.pov_character) AS pov_characters
FROM writing_drafts d
LEFT JOIN projects p ON p.id = d.project_id
GROUP BY d.project_id;

-- Feedback queue: open feedback items across all drafts, ordered by recency.
DROP VIEW IF EXISTS v_feedback_queue;
CREATE VIEW v_feedback_queue AS
SELECT
    df.id AS feedback_id,
    df.draft_id,
    d.title AS draft_title,
    d.project_id,
    p.name AS project_name,
    df.version_number,
    df.feedback_type,
    df.author,
    df.highlighted_text,
    df.content,
    df.status,
    df.created_at,
    CAST(julianday('now') - julianday(df.created_at) AS INTEGER) AS days_open
FROM draft_feedback df
JOIN writing_drafts d ON d.id = df.draft_id
LEFT JOIN projects p ON p.id = d.project_id
WHERE df.status = 'open'
ORDER BY df.created_at DESC;

-- Lore coverage: which creative_context entries are referenced by drafts.
DROP VIEW IF EXISTS v_lore_coverage;
CREATE VIEW v_lore_coverage AS
SELECT
    cc.id AS context_id,
    cc.context_type,
    cc.title AS context_title,
    cc.project_id,
    COUNT(dll.id) AS draft_references,
    GROUP_CONCAT(DISTINCT d.title) AS referencing_drafts,
    GROUP_CONCAT(DISTINCT dll.link_type) AS link_types
FROM creative_context cc
LEFT JOIN draft_lore_links dll ON dll.context_id = cc.id
LEFT JOIN writing_drafts d ON d.id = dll.draft_id
GROUP BY cc.id;

-- Character appearances: which characters appear across which drafts.
DROP VIEW IF EXISTS v_character_appearances;
CREATE VIEW v_character_appearances AS
SELECT
    dc.character_name,
    COUNT(DISTINCT dc.draft_id) AS draft_count,
    SUM(CASE WHEN dc.role = 'pov' THEN 1 ELSE 0 END) AS pov_count,
    SUM(CASE WHEN dc.role = 'featured' THEN 1 ELSE 0 END) AS featured_count,
    SUM(CASE WHEN dc.role = 'mentioned' THEN 1 ELSE 0 END) AS mentioned_count,
    GROUP_CONCAT(DISTINCT d.title) AS appears_in,
    GROUP_CONCAT(DISTINCT d.project_id) AS project_ids
FROM draft_characters dc
JOIN writing_drafts d ON d.id = dc.draft_id
GROUP BY dc.character_name
ORDER BY draft_count DESC;
