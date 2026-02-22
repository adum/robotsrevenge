# Robot's Revenge

Deployable campaign build for Cloudflare Pages (no in-browser generator).

## Prerequisites

- Python 3.10+ (for build and level generation scripts)
- Node.js + npm (for Wrangler CLI)
- Cloudflare account with Pages enabled
- Wrangler CLI installed and authenticated:

```bash
npm install -g wrangler
wrangler login
```

## Deployment Flow

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

Default project name is `robots-revenge`:

```bash
./scripts/deploy_pages.sh
```

To use a different Pages project:

```bash
CF_PAGES_PROJECT=my-project-name ./scripts/deploy_pages.sh
```

This script runs:

1. `python3 scripts/build_distribution.py`
2. `wrangler pages deploy dist --project-name <name> --functions functions`

## Local Preview (optional)

Build first, then run a local Pages preview with Functions:

```bash
python3 scripts/build_distribution.py
wrangler pages dev dist --functions functions
```

## Runtime Notes

- Playable UI is `play.html` (shipped as `index.html` in `dist/`)
- Verification API is `functions/api/submit.js` (`POST /api/submit`)
- API currently validates submissions against level files and simulator rules; persistence to a database can be added later

