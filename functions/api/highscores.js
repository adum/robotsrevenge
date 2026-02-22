function withCors(headers = {}) {
  return {
    "access-control-allow-origin": "*",
    "access-control-allow-methods": "GET, OPTIONS",
    "access-control-allow-headers": "content-type",
    ...headers,
  };
}

function json(payload, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: withCors({
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store",
    }),
  });
}

function getDb(context) {
  const db = context && context.env ? context.env.DB : null;
  if (!db || typeof db.prepare !== "function") {
    throw new Error("D1 binding DB is not configured.");
  }
  return db;
}

function parseLimit(requestUrl) {
  const url = new URL(requestUrl);
  const raw = url.searchParams.get("limit");
  let value = parseInt(raw ?? "200", 10);
  if (!Number.isFinite(value)) {
    value = 200;
  }
  return Math.max(1, Math.min(1000, value));
}

async function fetchHighscores(context, limit) {
  const db = getDb(context);
  const query = `
    WITH passed AS (
      SELECT player_id, level_number, submitted_at
      FROM submission_results
      WHERE result = 'escape'
    ),
    tops AS (
      SELECT player_id, MAX(level_number) AS top_level
      FROM passed
      GROUP BY player_id
    )
    SELECT
      tops.player_id AS player_id,
      tops.top_level AS top_level,
      MIN(passed.submitted_at) AS reached_at
    FROM tops
    JOIN passed
      ON passed.player_id = tops.player_id
     AND passed.level_number = tops.top_level
    GROUP BY tops.player_id, tops.top_level
    ORDER BY tops.top_level DESC, reached_at ASC, tops.player_id ASC
    LIMIT ?
  `;
  const result = await db.prepare(query).bind(limit).all();
  return Array.isArray(result.results) ? result.results : [];
}

export async function onRequestOptions() {
  return new Response(null, { status: 204, headers: withCors() });
}

export async function onRequestGet(context) {
  try {
    const limit = parseLimit(context.request.url);
    const rows = await fetchHighscores(context, limit);
    return json({
      ok: true,
      count: rows.length,
      rows,
      generated_at: new Date().toISOString(),
    });
  } catch (error) {
    return json(
      {
        ok: false,
        error: `Unable to load highscores: ${error && error.message ? error.message : String(error)}`,
      },
      500
    );
  }
}
