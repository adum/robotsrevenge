CREATE TABLE IF NOT EXISTS submission_results (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  player_id TEXT NOT NULL,
  level_number INTEGER NOT NULL,
  program TEXT NOT NULL,
  result TEXT NOT NULL,
  submitted_at TEXT NOT NULL,
  solution_hash TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_submission_results_player_level
  ON submission_results(player_id, level_number);

CREATE INDEX IF NOT EXISTS idx_submission_results_submitted_at
  ON submission_results(submitted_at);

CREATE INDEX IF NOT EXISTS idx_submission_results_solution_hash
  ON submission_results(solution_hash);
