import { Router } from 'express';
import { OAuth2Client } from 'google-auth-library';
import { randomBytes } from 'crypto';

const router = Router();

const GOOGLE_CLIENT_ID = process.env.GOOGLE_CLIENT_ID || '';
const GOOGLE_CLIENT_SECRET = process.env.GOOGLE_CLIENT_SECRET || '';
const BASE_URL = process.env.BASE_URL || 'http://localhost:3200';
const ADMIN_EMAIL = process.env.ADMIN_EMAIL || '';

const oauth2Client = new OAuth2Client(
  GOOGLE_CLIENT_ID,
  GOOGLE_CLIENT_SECRET,
  `${BASE_URL}/api/auth/callback`
);

// Generate login URL — if called with invite token, redirect directly to Google
// (invite links hit this endpoint raw from the browser, not via JS fetch)
router.get('/login', (req, res) => {
  const url = oauth2Client.generateAuthUrl({
    access_type: 'offline',
    scope: ['openid', 'email', 'profile'],
    state: req.query.invite || '',
  });

  // If there's an invite token or Accept header isn't JSON, redirect directly
  if (req.query.invite || !req.headers.accept?.includes('application/json')) {
    return res.redirect(url);
  }

  // Otherwise return JSON for the React frontend's fetch call
  res.json({ url });
});

// Google OAuth callback
router.get('/callback', async (req, res) => {
  const { code, state: inviteToken } = req.query;
  const db = req.app.locals.db;

  try {
    const { tokens } = await oauth2Client.getToken(code);
    const ticket = await oauth2Client.verifyIdToken({
      idToken: tokens.id_token,
      audience: GOOGLE_CLIENT_ID,
    });
    const payload = ticket.getPayload();
    const { email, name, picture, sub: googleId } = payload;

    // Check if user exists
    let user = db.prepare('SELECT * FROM dashboard_users WHERE email = ?').get(email);

    if (!user) {
      // Check if this is the admin email (auto-create as admin)
      if (email === ADMIN_EMAIL) {
        db.prepare(`
          INSERT INTO dashboard_users (email, name, picture, google_id, role)
          VALUES (?, ?, ?, ?, 'admin')
        `).run(email, name, picture, googleId);
        user = db.prepare('SELECT * FROM dashboard_users WHERE email = ?').get(email);
      }
      // Check if they have a valid invite
      else if (inviteToken) {
        const invite = db.prepare(`
          SELECT * FROM dashboard_invites
          WHERE token = ? AND email = ? AND accepted_at IS NULL AND expires_at > datetime('now')
        `).get(inviteToken, email);

        if (invite) {
          db.prepare(`
            INSERT INTO dashboard_users (email, name, picture, google_id, role, invited_by)
            VALUES (?, ?, ?, ?, ?, ?)
          `).run(email, name, picture, googleId, invite.role, invite.invited_by);

          db.prepare(`
            UPDATE dashboard_invites SET accepted_at = datetime('now') WHERE id = ?
          `).run(invite.id);

          user = db.prepare('SELECT * FROM dashboard_users WHERE email = ?').get(email);
        }
      }

      if (!user) {
        // Redirect to frontend with error
        return res.redirect('/?error=unauthorized');
      }
    }

    // Update profile and last login
    db.prepare(`
      UPDATE dashboard_users SET name = ?, picture = ?, google_id = ?, last_login_at = datetime('now')
      WHERE id = ?
    `).run(name, picture, googleId, user.id);

    // Create session
    const sessionToken = randomBytes(32).toString('hex');
    const expiresAt = new Date(Date.now() + 30 * 24 * 60 * 60 * 1000).toISOString(); // 30 days
    db.prepare(`
      INSERT INTO dashboard_sessions (user_id, token, expires_at) VALUES (?, ?, ?)
    `).run(user.id, sessionToken, expiresAt);

    // Set cookie and redirect
    res.cookie('session', sessionToken, {
      httpOnly: true,
      secure: process.env.NODE_ENV === 'production',
      sameSite: 'lax',
      maxAge: 30 * 24 * 60 * 60 * 1000,
    });

    res.redirect('/');
  } catch (error) {
    console.error('Auth error:', error.message);
    res.redirect('/?error=auth_failed');
  }
});

// Get current user
router.get('/me', (req, res) => {
  const db = req.app.locals.db;

  // Dev mode: auto-authenticate as admin when no Google OAuth configured
  if (!GOOGLE_CLIENT_ID && process.env.NODE_ENV !== 'production') {
    // Ensure admin user exists
    let admin = db.prepare("SELECT * FROM dashboard_users WHERE role = 'admin'").get();
    if (!admin) {
      db.prepare(`
        INSERT INTO dashboard_users (email, name, role)
        VALUES ('dev@localhost', 'Alex (dev mode)', 'admin')
      `).run();
      admin = db.prepare("SELECT * FROM dashboard_users WHERE role = 'admin'").get();
    }
    return res.json({
      user: { id: admin.id, email: admin.email, name: admin.name, picture: null, role: admin.role },
    });
  }

  const token = req.cookies?.session;
  if (!token) return res.json({ user: null });

  const session = db.prepare(`
    SELECT s.*, u.* FROM dashboard_sessions s
    JOIN dashboard_users u ON u.id = s.user_id
    WHERE s.token = ? AND s.expires_at > datetime('now')
  `).get(token);

  if (!session) return res.json({ user: null });

  res.json({
    user: {
      id: session.user_id,
      email: session.email,
      name: session.name,
      picture: session.picture,
      role: session.role,
    },
  });
});

// Logout
router.post('/logout', (req, res) => {
  const db = req.app.locals.db;
  const token = req.cookies?.session;
  if (token) {
    db.prepare('DELETE FROM dashboard_sessions WHERE token = ?').run(token);
  }
  res.clearCookie('session');
  res.json({ ok: true });
});

// Create invite (admin/reviewer only)
router.post('/invite', requireAuth, (req, res) => {
  const db = req.app.locals.db;
  const { email, role = 'reviewer' } = req.body;

  if (!['admin', 'reviewer'].includes(req.user.role)) {
    return res.status(403).json({ error: 'Only admins and reviewers can invite' });
  }

  const token = randomBytes(24).toString('hex');
  const expiresAt = new Date(Date.now() + 7 * 24 * 60 * 60 * 1000).toISOString(); // 7 days

  db.prepare(`
    INSERT INTO dashboard_invites (email, role, token, invited_by, expires_at)
    VALUES (?, ?, ?, ?, ?)
  `).run(email, role, token, req.user.id, expiresAt);

  const inviteUrl = `${BASE_URL}/api/auth/login?invite=${token}`;
  res.json({ inviteUrl, token, expiresAt });
});

// Auth middleware
export function requireAuth(req, res, next) {
  const db = req.app.locals.db;

  // Dev mode bypass
  if (!GOOGLE_CLIENT_ID && process.env.NODE_ENV !== 'production') {
    const admin = db.prepare("SELECT id as user_id, email, name, role FROM dashboard_users WHERE role = 'admin'").get();
    if (admin) {
      req.user = admin;
      return next();
    }
  }

  const token = req.cookies?.session;
  if (!token) return res.status(401).json({ error: 'Not authenticated' });

  const session = db.prepare(`
    SELECT s.user_id, u.email, u.name, u.role FROM dashboard_sessions s
    JOIN dashboard_users u ON u.id = s.user_id
    WHERE s.token = ? AND s.expires_at > datetime('now')
  `).get(token);

  if (!session) return res.status(401).json({ error: 'Session expired' });

  req.user = session;
  next();
}

export { router as authRouter };
