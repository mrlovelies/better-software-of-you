import { Router } from 'express';

const router = Router();

// List forecasts
router.get('/', (req, res) => {
  const db = req.app.locals.db;
  const { status, min_autonomy, sort, limit = 50 } = req.query;

  let where = ['1=1'];
  let params = [];

  if (status) {
    where.push("status = ?");
    params.push(status);
  }
  if (min_autonomy) {
    where.push("autonomy_score >= ?");
    params.push(parseFloat(min_autonomy));
  }

  const orderBy = sort === 'autonomy' ? 'autonomy_score DESC'
    : sort === 'mrr' ? 'estimated_mrr_high DESC'
    : 'composite_score DESC';

  params.push(parseInt(limit));

  const forecasts = db.prepare(`
    SELECT * FROM harvest_forecasts
    WHERE ${where.join(' AND ')}
    ORDER BY ${orderBy}
    LIMIT ?
  `).all(...params);

  res.json({ forecasts });
});

// Approve a forecast
router.post('/:id/approve', (req, res) => {
  const db = req.app.locals.db;
  const { notes } = req.body;
  db.prepare(`
    UPDATE harvest_forecasts SET status = 'approved', human_notes = ?, updated_at = datetime('now')
    WHERE id = ?
  `).run(notes || `Approved by ${req.user.name}`, parseInt(req.params.id));
  res.json({ ok: true });
});

// Kill a forecast
router.post('/:id/kill', (req, res) => {
  const db = req.app.locals.db;
  const { reason } = req.body;
  db.prepare(`
    UPDATE harvest_forecasts SET status = 'killed', human_notes = ?, updated_at = datetime('now')
    WHERE id = ?
  `).run(reason || `Killed by ${req.user.name}`, parseInt(req.params.id));
  res.json({ ok: true });
});

export { router as forecastsRouter };
