#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 || $# -gt 3 ]]; then
  echo "Usage: $0 <level-number> [output-svg-path] [cell-size]" >&2
  echo "Example: $0 198" >&2
  exit 2
fi

level_num="$1"
#out_svg="${2:-/tmp/level_${level_num}_trace.svg}"
out_svg=/tmp/test_trace.svg
cell_size="${3:-4}"

python3 visualize_level.py \
  "levels/${level_num}.level" \
  --solution-file "solutions/${level_num}.solution.json" \
  --svg-out "${out_svg}" \
  --cell-size "${cell_size}"

echo "Wrote: ${out_svg}"
