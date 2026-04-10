// POST /api/islands/submit — Handle form submissions from island components
//
// Stores submissions in the island_submissions table for later retrieval.
// Business owners can view submissions via the dashboard.

export async function onRequestPost(context) {
  const db = context.env.DB;

  let body;
  try {
    body = await context.request.json();
  } catch {
    return Response.json({ error: "Invalid request body" }, { status: 400 });
  }

  const { pageToken, islandId, formData } = body;

  if (!pageToken || !islandId || !formData) {
    return Response.json(
      { error: "Missing required fields: pageToken, islandId, formData" },
      { status: 400 }
    );
  }

  // Verify the page exists
  const page = await db
    .prepare("SELECT token FROM pages WHERE token = ?")
    .bind(pageToken)
    .first();

  if (!page) {
    return Response.json({ error: "Page not found" }, { status: 404 });
  }

  // Store the submission
  await db
    .prepare(
      "INSERT INTO island_submissions (page_token, island_id, form_data, created_at) VALUES (?, ?, ?, datetime('now'))"
    )
    .bind(pageToken, islandId, JSON.stringify(formData))
    .run();

  return Response.json({ ok: true, message: "Submission received" });
}
