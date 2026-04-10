import { Router } from 'express';

const router = Router();

// List signals with triage data, filterable
router.get('/', (req, res) => {
  const db = req.app.locals.db;
  const { status, industry, min_score, sort, limit = 50, offset = 0 } = req.query;

  let where = ['1=1'];
  let params = [];

  if (status === 'pending') {
    where.push("t.verdict = 'pending' AND t.composite_score IS NOT NULL AND t.human_reviewed = 0");
  } else if (status === 'approved') {
    where.push("t.verdict = 'approved'");
  } else if (status === 'rejected') {
    where.push("t.verdict = 'rejected'");
  }

  if (industry) {
    where.push("s.industry = ?");
    params.push(industry);
  }

  if (min_score) {
    where.push("t.composite_score >= ?");
    params.push(parseFloat(min_score));
  }

  const orderBy = sort === 'upvotes' ? 's.upvotes DESC'
    : sort === 'newest' ? 's.harvested_at DESC'
    : 't.composite_score DESC';

  params.push(parseInt(limit), parseInt(offset));

  const signals = db.prepare(`
    SELECT s.*, t.id as triage_id, t.verdict, t.composite_score,
           t.market_size_score, t.monetization_score, t.build_complexity_score,
           t.existing_solutions_score, t.soy_leaf_fit_score,
           t.existing_solutions, t.monetization_model, t.build_estimate,
           t.target_audience, t.human_reviewed, t.human_notes,
           t.verdict_reason
    FROM harvest_signals s
    LEFT JOIN harvest_triage t ON t.signal_id = s.id
    WHERE ${where.join(' AND ')}
    ORDER BY ${orderBy}
    LIMIT ? OFFSET ?
  `).all(...params);

  const total = db.prepare(`
    SELECT COUNT(*) as c FROM harvest_signals s
    LEFT JOIN harvest_triage t ON t.signal_id = s.id
    WHERE ${where.join(' AND ')}
  `).all(...params.slice(0, -2))[0]?.c || 0;

  res.json({ signals, total });
});

// Approve a signal
router.post('/:id/approve', (req, res) => {
  const db = req.app.locals.db;
  const { id } = req.params;
  const { notes } = req.body;

  db.prepare(`
    UPDATE harvest_triage SET verdict = 'approved', human_reviewed = 1,
      human_notes = ?, updated_at = datetime('now')
    WHERE signal_id = ?
  `).run(notes || `Approved by ${req.user.name}`, parseInt(id));

  db.prepare(`
    INSERT INTO activity_log (entity_type, entity_id, action, details, created_at)
    VALUES ('harvest_signal', ?, 'signal_approved', ?, datetime('now'))
  `).run(parseInt(id), `Approved via dashboard by ${req.user.name}`);

  res.json({ ok: true });
});

// Reject a signal
router.post('/:id/reject', (req, res) => {
  const db = req.app.locals.db;
  const { id } = req.params;
  const { reason } = req.body;
  const signalId = parseInt(id);

  const humanReason = reason || null;

  db.prepare(`
    UPDATE harvest_triage SET verdict = 'rejected', human_reviewed = 1,
      human_notes = ?, updated_at = datetime('now')
    WHERE signal_id = ?
  `).run(humanReason || `Rejected by ${req.user.name} (reason pending inference)`, signalId);

  // If no reason provided, queue LLM inference to figure out why
  if (!humanReason) {
    db.prepare(`
      INSERT OR IGNORE INTO rejection_inference_queue (signal_id, created_at)
      VALUES (?, datetime('now'))
    `).run(signalId);
  }

  res.json({ ok: true });
});

// Defer a signal
router.post('/:id/defer', (req, res) => {
  const db = req.app.locals.db;
  const { id } = req.params;
  const { notes } = req.body;

  db.prepare(`
    UPDATE harvest_triage SET verdict = 'deferred', human_reviewed = 1,
      human_notes = ?, updated_at = datetime('now')
    WHERE signal_id = ?
  `).run(notes || `Deferred by ${req.user.name}`, parseInt(id));

  res.json({ ok: true });
});

// Get distinct industries for filter
router.get('/industries', (req, res) => {
  const db = req.app.locals.db;
  const industries = db.prepare(`
    SELECT DISTINCT industry FROM harvest_signals WHERE industry IS NOT NULL ORDER BY industry
  `).all();
  res.json(industries.map(r => r.industry));
});

export { router as signalsRouter };
