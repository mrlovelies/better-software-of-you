import { Router } from 'express';

const router = Router();

// List competitive signals
router.get('/', (req, res) => {
  const db = req.app.locals.db;
  const { status, product, limit = 50, offset = 0 } = req.query;

  let where = ['complaint_summary IS NOT NULL'];
  let params = [];

  if (status === 'pending') {
    where.push("verdict = 'pending' AND human_reviewed = 0");
  } else if (status) {
    where.push("verdict = ?");
    params.push(status);
  }

  if (product) {
    where.push("LOWER(target_product) LIKE LOWER(?)");
    params.push(`%${product}%`);
  }

  params.push(parseInt(limit), parseInt(offset));

  const signals = db.prepare(`
    SELECT * FROM competitive_signals
    WHERE ${where.join(' AND ')}
    ORDER BY composite_score DESC
    LIMIT ? OFFSET ?
  `).all(...params);

  res.json({ signals });
});

// List tracked targets
router.get('/targets', (req, res) => {
  const db = req.app.locals.db;

  const targets = db.prepare(`
    SELECT
      ct.*,
      COUNT(cs.id) as signal_count,
      AVG(cs.sentiment_intensity) as avg_sentiment,
      AVG(cs.composite_score) as avg_composite
    FROM competitive_targets ct
    LEFT JOIN competitive_signals cs ON LOWER(cs.target_product) = LOWER(ct.product_name)
      AND cs.verdict != 'rejected'
    GROUP BY ct.id
    ORDER BY avg_composite DESC NULLS LAST
  `).all();

  res.json({ targets });
});

// Approve/reject competitive signal
router.post('/:id/approve', (req, res) => {
  const db = req.app.locals.db;
  const { notes } = req.body;
  db.prepare(`
    UPDATE competitive_signals SET verdict = 'opportunity', human_reviewed = 1,
      human_notes = ?, updated_at = datetime('now')
    WHERE id = ?
  `).run(notes || `Approved by ${req.user.name}`, parseInt(req.params.id));
  res.json({ ok: true });
});

router.post('/:id/reject', (req, res) => {
  const db = req.app.locals.db;
  const { reason } = req.body;
  const signalId = parseInt(req.params.id);

  db.prepare(`
    UPDATE competitive_signals SET verdict = 'rejected', human_reviewed = 1,
      human_notes = ?, updated_at = datetime('now')
    WHERE id = ?
  `).run(reason || `Rejected by ${req.user.name} (reason pending inference)`, signalId);

  if (!reason) {
    db.prepare(`
      INSERT OR IGNORE INTO rejection_inference_queue (signal_id, signal_type, created_at)
      VALUES (?, 'competitive', datetime('now'))
    `).run(signalId);
  }

  res.json({ ok: true });
});

export { router as competitiveRouter };
