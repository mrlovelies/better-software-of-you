import { readFileSync, existsSync as envExists } from 'fs';
import { join as envJoin, dirname as envDirname } from 'path';
import { fileURLToPath as envFileURLToPath } from 'url';

// Load .env from dashboard root
const __envDir = envDirname(envDirname(envFileURLToPath(import.meta.url)));
const envFile = envJoin(__envDir, '.env');
if (envExists(envFile)) {
  for (const line of readFileSync(envFile, 'utf8').split('\n')) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#') || !trimmed.includes('=')) continue;
    const [k, ...rest] = trimmed.split('=');
    const v = rest.join('=').trim();
    if (!process.env[k.trim()]) process.env[k.trim()] = v;
  }
}

import express from 'express';
import cors from 'cors';
import cookieParser from 'cookie-parser';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';
import { existsSync } from 'fs';
import { createDb } from './db.js';
import { authRouter, requireAuth } from './auth.js';
import { pipelineRouter } from './routes/pipeline.js';
import { signalsRouter } from './routes/signals.js';
import { competitiveRouter } from './routes/competitive.js';
import { forecastsRouter } from './routes/forecasts.js';
import { digestRouter } from './routes/digest.js';
import { buildsRouter } from './routes/builds.js';

const __dirname = dirname(fileURLToPath(import.meta.url));
const PORT = process.env.PORT || 3200;
const isDev = process.env.NODE_ENV !== 'production';

const app = express();
app.use(cors({ origin: true, credentials: true }));
app.use(express.json());
app.use(cookieParser());

// Database
const db = createDb();
app.locals.db = db;

// Auth routes (login, callback, invite — no auth required)
app.use('/api/auth', authRouter);

// Protected API routes
app.use('/api/pipeline', requireAuth, pipelineRouter);
app.use('/api/signals', requireAuth, signalsRouter);
app.use('/api/competitive', requireAuth, competitiveRouter);
app.use('/api/forecasts', requireAuth, forecastsRouter);
app.use('/api/builds', requireAuth, buildsRouter);

// Digest route — used by Discord notifier, authenticated via internal token
app.use('/api/digest', digestRouter);

// Serve React app from built dist
const distPath = join(__dirname, '..', 'dist');
if (existsSync(distPath)) {
  app.use(express.static(distPath));
  app.get('*', (req, res) => {
    if (!req.path.startsWith('/api')) {
      res.sendFile(join(distPath, 'index.html'));
    }
  });
}

app.listen(PORT, '0.0.0.0', () => {
  console.log(`Harvest Dashboard API running on :${PORT}`);
});
