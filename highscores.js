const refreshBtn = document.getElementById("refreshBtn");
const scoresMetaEl = document.getElementById("scoresMeta");
const scoresBodyEl = document.getElementById("scoresBody");

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function formatTimestamp(raw) {
  if (!raw) {
    return "-";
  }
  const date = new Date(raw);
  if (!Number.isFinite(date.getTime())) {
    return String(raw);
  }
  return date.toLocaleString();
}

function renderRows(rows) {
  if (!Array.isArray(rows) || rows.length === 0) {
    scoresBodyEl.innerHTML = '<tr><td colspan="4">No passed levels yet.</td></tr>';
    return;
  }
  scoresBodyEl.innerHTML = rows
    .map((row, index) => {
      const rank = index + 1;
      const playerId = escapeHtml(row.player_id || "");
      const topLevel = Number.isFinite(Number(row.top_level)) ? Number(row.top_level) : "-";
      const reachedAt = formatTimestamp(row.reached_at);
      return (
        "<tr>" +
        `<td>${rank}</td>` +
        `<td><code>${playerId}</code></td>` +
        `<td>${topLevel}</td>` +
        `<td>${escapeHtml(reachedAt)}</td>` +
        "</tr>"
      );
    })
    .join("");
}

async function loadHighscores() {
  refreshBtn.disabled = true;
  scoresMetaEl.textContent = "Loading highscores...";
  try {
    const response = await fetch("/api/highscores?limit=250", { cache: "no-store" });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || !data || data.ok !== true) {
      const message = data && data.error ? data.error : `Could not load highscores (${response.status}).`;
      scoresMetaEl.textContent = message;
      scoresBodyEl.innerHTML = '<tr><td colspan="4">Unable to load highscores.</td></tr>';
      return;
    }

    renderRows(data.rows || []);
    const generatedAt = formatTimestamp(data.generated_at);
    scoresMetaEl.innerHTML =
      `<div><strong>Players:</strong> ${data.count ?? 0}</div>` +
      `<div><strong>Updated:</strong> ${escapeHtml(generatedAt)}</div>`;
  } catch (error) {
    scoresMetaEl.textContent = `Could not load highscores: ${error && error.message ? error.message : String(error)}`;
    scoresBodyEl.innerHTML = '<tr><td colspan="4">Unable to load highscores.</td></tr>';
  } finally {
    refreshBtn.disabled = false;
  }
}

refreshBtn.addEventListener("click", loadHighscores);
void loadHighscores();
