import { Router } from 'express';

const router = Router();

/**
 * Digest API — single source of truth for both dashboard and Discord notifications.
 * Returns pre-formatted digest data. Discord notifier calls this instead of querying DB directly.
 *
 * Auth: internal token (DIGEST_TOKEN env var) — not user auth.
 */

function requireDigestToken(req, res, next) {
  const token = req.headers['x-digest-token'] || req.query.token;
  const expected = process.env.DIGEST_TOKEN;

  // In dev or if no token configured, allow access
  if (!expected || token === expected) return next();
  return res.status(403).json({ error: 'Invalid digest token' });
}

// Full pipeline digest — used for Discord summary
router.get('/summary', requireDigestToken, (req, res) => {
  const db = req.app.locals.db;

  const funnel = {
    harvested: db.prepare('SELECT COUNT(*) as c FROM harvest_signals').get().c,
    approved: db.prepare("SELECT COUNT(*) as c FROM harvest_triage WHERE verdict = 'approved'").get().c,
    built: db.prepare('SELECT COUNT(*) as c FROM harvest_builds').get().c,
    shipped: db.prepare("SELECT COUNT(*) as c FROM harvest_builds WHERE status = 'shipped'").get().c,
    revenue: db.prepare('SELECT COALESCE(SUM(revenue), 0) as r FROM harvest_builds').get().r,
  };

  const pending = {
    signals: db.prepare("SELECT COUNT(*) as c FROM harvest_triage WHERE verdict = 'pending' AND composite_score IS NOT NULL AND human_reviewed = 0").get().c,
    competitive: db.prepare("SELECT COUNT(*) as c FROM competitive_signals WHERE verdict = 'pending' AND human_reviewed = 0 AND complaint_summary IS NOT NULL").get().c,
    forecasts: db.prepare("SELECT COUNT(*) as c FROM harvest_forecasts WHERE status = 'idea'").get().c,
  };

  const last_run = db.prepare("SELECT value FROM soy_meta WHERE key = 'pipeline_last_run'").get();

  res.json({ funnel, pending, last_run: last_run?.value });
});

// Signals digest — top signals awaiting review
router.get('/signals', requireDigestToken, (req, res) => {
  const db = req.app.locals.db;
  const limit = parseInt(req.query.limit) || 5;

  const signals = db.prepare(`
    SELECT s.id, s.extracted_pain, s.industry, s.subreddit, s.upvotes, s.source_url,
           t.composite_score, t.market_size_score, t.monetization_score,
           t.existing_solutions_score
    FROM harvest_signals s
    JOIN harvest_triage t ON t.signal_id = s.id
    WHERE t.verdict = 'pending' AND t.composite_score IS NOT NULL AND t.human_reviewed = 0
    ORDER BY t.composite_score DESC
    LIMIT ?
  `).all(limit);

  res.json({ signals, total: signals.length });
});

// Competitive digest — top competitive signals
router.get('/competitive', requireDigestToken, (req, res) => {
  const db = req.app.locals.db;
  const limit = parseInt(req.query.limit) || 5;

  const signals = db.prepare(`
    SELECT id, target_product, target_category, complaint_type, complaint_summary,
           composite_score, switchability_score, build_advantage_score,
           upvotes, subreddit
    FROM competitive_signals
    WHERE verdict = 'pending' AND human_reviewed = 0 AND complaint_summary IS NOT NULL
    ORDER BY composite_score DESC
    LIMIT ?
  `).all(limit);

  res.json({ signals });
});

// Forecasts digest
router.get('/forecasts', requireDigestToken, (req, res) => {
  const db = req.app.locals.db;
  const limit = parseInt(req.query.limit) || 5;

  const forecasts = db.prepare(`
    SELECT id, title, description, origin_type, composite_score, autonomy_score,
           revenue_model, estimated_mrr_low, estimated_mrr_high, estimated_build_days
    FROM harvest_forecasts
    WHERE status = 'idea'
    ORDER BY composite_score DESC
    LIMIT ?
  `).all(limit);

  res.json({ forecasts });
});

export { router as digestRouter };
