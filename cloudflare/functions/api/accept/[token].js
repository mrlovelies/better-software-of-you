// POST /api/accept/:token — Process acceptance (flip preview to live)
//
// The acceptance_token is a UUID sent in the outreach email. When the
// prospect clicks "Go Live", the acceptance page POSTs here. We set
// is_live=1 and clear the token so it can't be reused.

export async function onRequestPost(context) {
  const { token } = context.params;
  const db = context.env.DB;

  // Find page by acceptance_token
  const page = await db
    .prepare(
      "SELECT token, title, is_live, acceptance_token FROM pages WHERE acceptance_token = ?"
    )
    .bind(token)
    .first();

  if (!page) {
    return Response.json(
      { error: "Invalid or expired acceptance link" },
      { status: 404 }
    );
  }

  if (page.is_live) {
    return Response.json(
      { error: "This site is already live", url: `/p/${page.token}` },
      { status: 409 }
    );
  }

  // Flip to live, clear acceptance token (one-time use)
  await db
    .prepare(
      "UPDATE pages SET is_live = 1, acceptance_token = NULL, updated_at = datetime('now') WHERE token = ?"
    )
    .bind(page.token)
    .run();

  return Response.json({
    ok: true,
    message: "Your new website is live!",
    url: `/p/${page.token}`,
    title: page.title,
  });
}
