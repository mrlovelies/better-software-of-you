// GET /accept/:token — Acceptance landing page
//
// Shows the prospect their new site in an iframe with a "Make This My Website"
// button. Clicking the button POSTs to /api/accept/:token and the site goes
// live instantly.

export async function onRequestGet(context) {
  const { token } = context.params;
  const db = context.env.DB;

  // Look up by acceptance_token
  const page = await db
    .prepare(
      "SELECT token, title, is_live, acceptance_token FROM pages WHERE acceptance_token = ?"
    )
    .bind(token)
    .first();

  if (!page) {
    return new Response(expiredHTML(), {
      status: 404,
      headers: { "Content-Type": "text/html; charset=utf-8" },
    });
  }

  if (page.is_live) {
    // Already accepted — redirect to live page
    return Response.redirect(
      new URL(`/p/${page.token}`, context.request.url).toString(),
      302
    );
  }

  return new Response(acceptanceHTML(page.title, page.token, token), {
    status: 200,
    headers: {
      "Content-Type": "text/html; charset=utf-8",
      "Cache-Control": "no-cache, no-store, must-revalidate",
    },
  });
}

function escHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function acceptanceHTML(title, pageToken, acceptToken) {
  return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta name="robots" content="noindex, nofollow">
  <title>${escHtml(title)} — Preview</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
  <style>body { font-family: 'Inter', sans-serif; }</style>
</head>
<body class="bg-zinc-100 min-h-screen">

  <!-- Top bar -->
  <div id="top-bar" class="fixed top-0 left-0 right-0 z-50 bg-white border-b border-zinc-200 shadow-sm">
    <div class="max-w-5xl mx-auto px-4 py-3 flex items-center justify-between">
      <div>
        <h1 class="text-lg font-semibold text-zinc-900">${escHtml(title)}</h1>
        <p class="text-sm text-zinc-500">Your new website is ready. Preview it below.</p>
      </div>
      <div class="flex items-center gap-3">
        <button id="go-live-btn" onclick="goLive()"
          class="px-6 py-2.5 bg-emerald-600 text-white font-semibold rounded-lg
                 hover:bg-emerald-700 transition-colors shadow-sm">
          Make This My Website
        </button>
      </div>
    </div>
  </div>

  <!-- Preview iframe -->
  <div class="pt-20">
    <iframe src="/p/${escHtml(pageToken)}" class="w-full border-0"
      style="height: calc(100vh - 80px);"
      title="Website preview"></iframe>
  </div>

  <!-- Success overlay (hidden by default) -->
  <div id="success-overlay" class="hidden fixed inset-0 z-[60] bg-black/50 flex items-center justify-center p-4">
    <div class="bg-white rounded-2xl shadow-2xl p-8 max-w-md w-full text-center">
      <div class="mx-auto w-16 h-16 rounded-full bg-emerald-100 flex items-center justify-center mb-4">
        <svg class="w-8 h-8 text-emerald-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"></path>
        </svg>
      </div>
      <h2 class="text-xl font-bold text-zinc-900 mb-2">Your website is live!</h2>
      <p class="text-zinc-600 mb-6">${escHtml(title)} is now online and ready for visitors.</p>
      <a id="live-link" href="/p/${escHtml(pageToken)}"
        class="inline-block px-6 py-3 bg-zinc-900 text-white font-semibold rounded-lg hover:bg-zinc-800 transition-colors">
        View Your Website →
      </a>
    </div>
  </div>

  <script>
    async function goLive() {
      const btn = document.getElementById('go-live-btn');
      btn.disabled = true;
      btn.textContent = 'Going live...';

      try {
        const resp = await fetch('/api/accept/${escHtml(acceptToken)}', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
        });
        const data = await resp.json();

        if (data.ok) {
          document.getElementById('success-overlay').classList.remove('hidden');
          document.getElementById('top-bar').classList.add('hidden');
        } else {
          btn.textContent = data.error || 'Something went wrong';
          btn.classList.replace('bg-emerald-600', 'bg-red-600');
          setTimeout(() => {
            btn.disabled = false;
            btn.textContent = 'Make This My Website';
            btn.classList.replace('bg-red-600', 'bg-emerald-600');
          }, 3000);
        }
      } catch (e) {
        btn.textContent = 'Network error — try again';
        btn.classList.replace('bg-emerald-600', 'bg-red-600');
        setTimeout(() => {
          btn.disabled = false;
          btn.textContent = 'Make This My Website';
          btn.classList.replace('bg-red-600', 'bg-emerald-600');
        }, 3000);
      }
    }
  </script>
</body>
</html>`;
}

function expiredHTML() {
  return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Link Expired</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
  <style>body { font-family: 'Inter', sans-serif; }</style>
</head>
<body class="bg-zinc-50 min-h-screen flex items-center justify-center p-4">
  <div class="text-center max-w-sm">
    <div class="mx-auto w-12 h-12 rounded-full bg-zinc-100 flex items-center justify-center mb-4">
      <svg class="w-6 h-6 text-zinc-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5"
          d="M12 9v3.75m9-.75a9 9 0 11-18 0 9 9 0 0118 0zm-9 3.75h.008v.008H12v-.008z" />
      </svg>
    </div>
    <h1 class="text-lg font-semibold text-zinc-900 mb-1">This link is no longer valid</h1>
    <p class="text-sm text-zinc-500">It may have already been used or has expired.</p>
  </div>
</body>
</html>`;
}
