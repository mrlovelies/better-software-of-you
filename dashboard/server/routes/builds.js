import { Router } from 'express';
import { readdirSync, readFileSync, existsSync, statSync } from 'fs';
import { join } from 'path';
import { execSync } from 'child_process';

const router = Router();
const PLUGIN_ROOT = process.env.CLAUDE_PLUGIN_ROOT || join(import.meta.dirname, '../../..');
const BUILDS_DIR = join(PLUGIN_ROOT, 'builds');

// List all builds with status
router.get('/', (req, res) => {
  if (!existsSync(BUILDS_DIR)) return res.json({ builds: [] });

  const builds = [];
  for (const dir of readdirSync(BUILDS_DIR)) {
    const metaPath = join(BUILDS_DIR, dir, '.build-meta.json');
    if (!existsSync(metaPath)) continue;

    try {
      const meta = JSON.parse(readFileSync(metaPath, 'utf8'));
      
      // Count source files (search packages/ or root, skip if neither exists)
      let sourceFiles = 0;
      try {
        const searchDir = existsSync(join(BUILDS_DIR, dir, 'packages')) 
          ? join(BUILDS_DIR, dir, 'packages')
          : join(BUILDS_DIR, dir);
        const result = execSync(
          `find ${searchDir} \( -name "*.ts" -o -name "*.tsx" -o -name "*.js" -o -name "*.jsx" \) ! -path "*/node_modules/*" ! -path "*/.gsd/*" | wc -l`,
          { encoding: 'utf8', timeout: 5000 }
        );
        sourceFiles = parseInt(result.trim()) || 0;
      } catch {}

      // Check log size for activity indicator
      let logLines = 0;
      const logPath = join(BUILDS_DIR, dir, 'build.log');
      if (existsSync(logPath)) {
        try {
          const stat = statSync(logPath);
          logLines = Math.round(stat.size / 200); // rough estimate
        } catch {}
      }

      // Check GSD state (find first available milestone roadmap)
      let gsdState = null;
      try {
        const milestonesDir = join(BUILDS_DIR, dir, '.gsd', 'milestones');
        if (existsSync(milestonesDir)) {
          for (const m of readdirSync(milestonesDir).sort()) {
            const rp = join(milestonesDir, m, m + '-ROADMAP.md');
            if (existsSync(rp)) {
              const roadmap = readFileSync(rp, 'utf8');
              const totalSlices = (roadmap.match(/^- \[/gm) || []).length;
              const doneSlices = (roadmap.match(/^- \[x\]/gm) || []).length;
              gsdState = { milestone: m, totalSlices, doneSlices, progress: totalSlices > 0 ? Math.round(doneSlices / totalSlices * 100) : 0 };
              break;
            }
          }
        }
      } catch {}

      // Check if build is active — log freshness + process check
      let isActive = false;
      try {
        const now = Date.now();
        for (const lp of [logPath, join(BUILDS_DIR, dir, 'planning.log')]) {
          if (existsSync(lp)) {
            const mtime = statSync(lp).mtimeMs;
            if (now - mtime < 120000) { isActive = true; break; }
          }
        }
        if (!isActive) {
          const ps = execSync('ps aux | grep "gsd" | grep -v grep | wc -l', { encoding: 'utf8', timeout: 3000 });
          isActive = parseInt(ps.trim()) > 0;
        }
      } catch {}

      // Derive variant label
      const variant = meta.variant || (dir.includes('build-c') ? 'stock_claude' : 
        (existsSync(join(BUILDS_DIR, dir, 'REVIEW-FEEDBACK.md')) ? 'gsd_with_persona_review' : 'gsd_baseline'));
      const variantLabels = {
        'gsd_baseline': 'A: GSD Headless',
        'gsd_with_persona_review': 'B: GSD + Persona Review',
        'stock_claude': 'C: Stock Claude',
      };

      builds.push({
        id: dir,
        ...meta,
        variant,
        variantLabel: variantLabels[variant] || variant,
        sourceFiles,
        logLines,
        gsdState,
        isActive,
      });
    } catch {}
  }

  // Sort by created_at descending
  builds.sort((a, b) => (b.created_at || '').localeCompare(a.created_at || ''));
  res.json({ builds });
});

// Get detailed build info
router.get('/:id', (req, res) => {
  const buildDir = join(BUILDS_DIR, req.params.id);
  const metaPath = join(buildDir, '.build-meta.json');

  if (!existsSync(metaPath)) return res.status(404).json({ error: 'Build not found' });

  const meta = JSON.parse(readFileSync(metaPath, 'utf8'));

  // Read source file list
  let sourceFiles = [];
  try {
    const result = execSync(
      `find ${join(buildDir, 'packages')} -name "*.ts" -o -name "*.tsx" -o -name "*.js" -o -name "*.jsx" | grep -v node_modules | sort`,
      { encoding: 'utf8', timeout: 5000 }
    );
    sourceFiles = result.trim().split('\n').filter(Boolean).map(f => f.replace(buildDir + '/', ''));
  } catch {}

  // Read GSD roadmap
  let roadmap = null;
  const roadmapPath = join(buildDir, '.gsd', 'milestones', 'M001', 'M001-ROADMAP.md');
  if (existsSync(roadmapPath)) {
    roadmap = readFileSync(roadmapPath, 'utf8');
  }

  // Read requirements
  let requirements = null;
  const reqPath = join(buildDir, 'REQUIREMENTS.md');
  if (existsSync(reqPath)) {
    requirements = readFileSync(reqPath, 'utf8');
  }

  // Read latest log entries (last 50 meaningful events)
  let recentActivity = [];
  const logPath = join(buildDir, 'build.log');
  if (existsSync(logPath)) {
    try {
      const log = readFileSync(logPath, 'utf8');
      const lines = log.split('\n').filter(l => l.startsWith('{'));
      for (const line of lines.slice(-200)) {
        try {
          const d = JSON.parse(line);
          if (d.type === 'turn_end') {
            recentActivity.push({ type: 'turn_end', timestamp: Date.now() });
          } else if (d.type === 'message_update') {
            const evt = d.assistantMessageEvent;
            if (evt?.type === 'text_delta') {
              const content = evt.partial?.content;
              if (content) {
                for (const c of content) {
                  if (c.type === 'text' && c.text.length > 50) {
                    recentActivity.push({ type: 'text', preview: c.text.slice(-200) });
                  }
                }
              }
            }
          }
        } catch {}
      }
      recentActivity = recentActivity.slice(-10);
    } catch {}
  }

  // Provisioned services
  const db = req.app.locals.db;
  const credentials = db.prepare(`
    SELECT service, key, metadata FROM service_credentials WHERE build_id = ?
  `).all(req.params.id);

  res.json({
    ...meta,
    sourceFiles,
    roadmap,
    requirements,
    recentActivity,
    credentials: credentials.map(c => ({ service: c.service, key: c.key, metadata: c.metadata })),
  });
});

export { router as buildsRouter };
