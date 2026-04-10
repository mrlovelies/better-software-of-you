import { Router } from 'express';

const router = Router();

// Pipeline overview — funnel stats, recent activity, health
router.get('/overview', (req, res) => {
  const db = req.app.locals.db;

  const funnel = {
    harvested: db.prepare('SELECT COUNT(*) as c FROM harvest_signals').get().c,
    passed_t1: db.prepare("SELECT COUNT(*) as c FROM harvest_triage WHERE verdict != 'rejected' OR verdict_reason NOT LIKE 'T1%'").get().c,
    scored: db.prepare('SELECT COUNT(*) as c FROM harvest_triage WHERE composite_score IS NOT NULL').get().c,
    approved: db.prepare("SELECT COUNT(*) as c FROM harvest_triage WHERE verdict = 'approved'").get().c,
    built: db.prepare('SELECT COUNT(*) as c FROM harvest_builds').get().c,
    shipped: db.prepare("SELECT COUNT(*) as c FROM harvest_builds WHERE status = 'shipped'").get().c,
    revenue: db.prepare('SELECT COALESCE(SUM(revenue), 0) as r FROM harvest_builds').get().r,
  };

  const pending_review = db.prepare(`
    SELECT COUNT(*) as c FROM harvest_triage
    WHERE verdict = 'pending' AND composite_score IS NOT NULL AND human_reviewed = 0
  `).get().c;

  const competitive = {
    total: db.prepare('SELECT COUNT(*) as c FROM competitive_signals WHERE complaint_summary IS NOT NULL').get().c,
    targets: db.prepare('SELECT COUNT(*) as c FROM competitive_targets').get().c,
    pending: db.prepare("SELECT COUNT(*) as c FROM competitive_signals WHERE verdict = 'pending' AND human_reviewed = 0 AND complaint_summary IS NOT NULL").get().c,
  };

  const forecasts = {
    total: db.prepare('SELECT COUNT(*) as c FROM harvest_forecasts').get().c,
    ideas: db.prepare("SELECT COUNT(*) as c FROM harvest_forecasts WHERE status = 'idea'").get().c,
    approved: db.prepare("SELECT COUNT(*) as c FROM harvest_forecasts WHERE status = 'approved'").get().c,
  };

  const triage_accuracy = db.prepare(`
    SELECT COUNT(*) as total, SUM(was_correct) as correct
    FROM triage_calibration
  `).get();

  const top_subreddits = db.prepare(`
    SELECT * FROM harvest_subreddit_stats
    WHERE signals_harvested >= 1
    ORDER BY yield_rate DESC LIMIT 10
  `).all();

  const top_industries = db.prepare(`
    SELECT * FROM harvest_industry_stats
    WHERE signals_found >= 1
    ORDER BY signals_approved DESC LIMIT 10
  `).all();

  const last_run = db.prepare("SELECT value FROM soy_meta WHERE key = 'pipeline_last_run'").get();

  const recent_evolution = db.prepare(`
    SELECT * FROM harvest_evolution_log ORDER BY created_at DESC LIMIT 5
  `).all();

  res.json({
    funnel,
    pending_review,
    competitive,
    forecasts,
    triage_accuracy: {
      total: triage_accuracy.total,
      correct: triage_accuracy.correct,
      rate: triage_accuracy.total > 0 ? triage_accuracy.correct / triage_accuracy.total : null,
    },
    top_subreddits,
    top_industries,
    last_run: last_run?.value,
    recent_evolution,
  });
});

export { router as pipelineRouter };
