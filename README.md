# Robot's Revenge

Deployable campaign build for Cloudflare Pages (no in-browser generator).

## Prerequisites

- Python 3.10+ (for build and level generation scripts)
- Node.js + npm (for Wrangler CLI)
- Cloudflare account with Pages enabled
- Wrangler available either globally or via `npx`:

```bash
npm install -g wrangler
npx wrangler login
```

## Deployment Flow

### 0. Configure D1 results database

Create a D1 database:

```bash
npx wrangler d1 create robotsrevenge-results
```

Copy the returned `database_id` and set the D1 binding in `wrangler.toml`:

```toml
[[d1_databases]]
binding = "DB"
database_name = "robotsrevenge-results"
database_id = "<your-d1-database-id>"
preview_database_id = "<your-d1-preview-database-id>"
```

Apply schema to the remote database:

```bash
npx wrangler d1 execute robotsrevenge-results --remote --file db/schema.sql
```

### 1. Prepare levels

`scripts/build_distribution.py` does **not** generate levels. It expects existing files in `levels/`.

Requirements for `levels/`:

- At least one file: `1.level`
- Numeric filenames only (`1.level`, `2.level`, ...)
- Contiguous sequence with no gaps

If needed, generate levels first (example for 100 levels):

```bash
python3 generate_levels.py 100
```

### 2. Build distribution bundle

```bash
python3 scripts/build_distribution.py
```

This creates `dist/` with:

- `index.html` (from `play.html`)
- runtime assets (`play.js`, `play.css`, `index.css`, `assets/`)
- copied levels under `dist/levels/`
- generated `dist/levels/manifest.json`

### 3. Deploy to Cloudflare Pages

Default project name is `robotsrevenge`:

```bash
./scripts/deploy_pages.sh
```

To use a different Pages project:

```bash
CF_PAGES_PROJECT=my-project-name ./scripts/deploy_pages.sh
```

This script runs:

1. `python3 scripts/build_distribution.py`
2. `wrangler pages deploy dist --project-name <name>` (or `npx wrangler ...`)

Note: Cloudflare Pages uploads `functions/` automatically when deploying from the repo root.

## Local Preview (optional)

Build first, then run a local Pages preview with Functions:

```bash
python3 scripts/build_distribution.py
npx wrangler pages dev dist
```

## Runtime Notes

- Playable UI is `play.html` (shipped as `index.html` in `dist/`)
- Verification API is `functions/api/submit.js` (`POST /api/submit`)
- Submission results are persisted in D1 table `submission_results` with:
  - `player_id`
  - `level_number`
  - `program`
  - `result`
  - `submitted_at`
  - `solution_hash` (SHA-256 of canonical program text)
