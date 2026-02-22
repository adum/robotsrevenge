#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

python3 scripts/build_distribution.py "$@"

PROJECT_NAME="${CF_PAGES_PROJECT:-robotsrevenge}"
if command -v wrangler >/dev/null 2>&1; then
  WRANGLER_CMD=(wrangler)
elif command -v npx >/dev/null 2>&1; then
  WRANGLER_CMD=(npx wrangler)
else
  echo "Error: neither 'wrangler' nor 'npx' is available in PATH." >&2
  exit 127
fi

echo "+ ${WRANGLER_CMD[*]} pages deploy dist --project-name ${PROJECT_NAME}"
"${WRANGLER_CMD[@]}" pages deploy dist --project-name "${PROJECT_NAME}"
