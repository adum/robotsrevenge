#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import random
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import sensejump_core as core


@dataclass(frozen=True)
class Candidate:
    generator: str
    source_level_id: int
    level_path: Path
    solution_path: Path
    size: int
    width: int
    height: int
    min_moves_to_exit: int | None
    min_direction_types_to_exit: int | None
    solution_steps: int | None
    level_hash: str | None
    solution_hash: str | None


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def level_id_from_path(path: Path) -> int | None:
    stem = path.name.split(".", 1)[0]
    if not stem.isdigit():
        return None
    return int(stem)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Combine per-generator best_of pools into one final consecutive level set. "
            "Selects approximately N levels per board size bucket."
        )
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=ROOT_DIR / "generated",
        help="Generated root directory containing <generator>/best_of (default: generated).",
    )
    parser.add_argument(
        "--best-of-subdir",
        type=str,
        default="best_of",
        help="Best-of subdirectory inside each generator folder (default: best_of).",
    )
    parser.add_argument(
        "--out-subdir",
        type=str,
        default="final",
        help="Output subdirectory under --root (default: final).",
    )
    parser.add_argument(
        "--levels-per-size-factor",
        type=float,
        default=2.0,
        help=(
            "Approximate target number of selected levels per board size bucket. "
            "Integer part is always selected; fractional part is sampled stochastically (default: 2.0)."
        ),
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Optional random seed. If omitted, a random seed is generated.",
    )
    parser.add_argument(
        "--generator",
        action="append",
        default=[],
        help="Filter generator ID(s) to include (can repeat). Default: all found generators.",
    )
    parser.add_argument(
        "--copy-mode",
        choices=("copy", "hardlink"),
        default="copy",
        help="How selected files are materialized (default: copy).",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing output subdirectory.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Do not write files; print summary and planned selection only.",
    )
    return parser


def discover_candidates(root: Path, best_of_subdir: str, generator_filters: set[str] | None) -> tuple[list[Candidate], dict[str, int]]:
    candidates: list[Candidate] = []
    skipped_counts: dict[str, int] = {
        "missing_solution": 0,
        "parse_error": 0,
        "non_square": 0,
    }

    if not root.exists():
        return candidates, skipped_counts

    generator_dirs = sorted(path for path in root.iterdir() if path.is_dir())
    for generator_dir in generator_dirs:
        generator_id = generator_dir.name
        if generator_filters is not None and generator_id not in generator_filters:
            continue

        best_root = generator_dir / best_of_subdir
        levels_dir = best_root / "levels"
        solutions_dir = best_root / "solutions"
        if not levels_dir.is_dir() or not solutions_dir.is_dir():
            continue

        for level_path in sorted(levels_dir.glob("*.level")):
            source_level_id = level_id_from_path(level_path)
            if source_level_id is None:
                continue
            solution_path = solutions_dir / f"{source_level_id}.solution.json"
            if not solution_path.exists():
                skipped_counts["missing_solution"] += 1
                continue

            try:
                level = core.parse_level(level_path.read_text(encoding="utf-8"))
                solution_data = json.loads(solution_path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                skipped_counts["parse_error"] += 1
                continue

            if level.width != level.height:
                skipped_counts["non_square"] += 1
                continue

            size = level.width
            min_moves_to_exit = solution_data.get("min_moves_to_exit")
            min_direction_types_to_exit = solution_data.get("min_direction_types_to_exit")
            solution_steps = solution_data.get("solution_steps")
            level_hash = solution_data.get("level_hash")
            solution_hash = solution_data.get("solution_hash")

            candidates.append(
                Candidate(
                    generator=generator_id,
                    source_level_id=source_level_id,
                    level_path=level_path,
                    solution_path=solution_path,
                    size=size,
                    width=level.width,
                    height=level.height,
                    min_moves_to_exit=(int(min_moves_to_exit) if min_moves_to_exit is not None else None),
                    min_direction_types_to_exit=(
                        int(min_direction_types_to_exit) if min_direction_types_to_exit is not None else None
                    ),
                    solution_steps=(int(solution_steps) if solution_steps is not None else None),
                    level_hash=(str(level_hash) if level_hash is not None else None),
                    solution_hash=(str(solution_hash) if solution_hash is not None else None),
                )
            )
    return candidates, skipped_counts


def picks_for_size_bucket(levels_per_size_factor: float, rng: random.Random) -> int:
    base = int(math.floor(levels_per_size_factor))
    frac = levels_per_size_factor - base
    picks = base
    if frac > 0.0 and rng.random() < frac:
        picks += 1
    return max(0, picks)


def materialize_candidate(src: Path, dst: Path, copy_mode: str) -> None:
    if copy_mode == "hardlink":
        dst.hardlink_to(src)
    else:
        shutil.copy2(src, dst)


def main(argv: list[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.levels_per_size_factor < 0:
        parser.error("--levels-per-size-factor must be >= 0.")

    generator_filters = set(args.generator) if args.generator else None
    seed = args.seed if args.seed is not None else random.SystemRandom().randrange(1, 2**63)
    rng = random.Random(seed)

    candidates, skipped_counts = discover_candidates(args.root, args.best_of_subdir, generator_filters)
    if not candidates:
        print(
            f"No candidate levels found under {args.root}/*/{args.best_of_subdir}/levels",
            file=sys.stderr,
        )
        return 1

    by_size: dict[int, list[Candidate]] = {}
    for candidate in candidates:
        by_size.setdefault(candidate.size, []).append(candidate)

    selected: list[Candidate] = []
    size_selection_summary: dict[int, dict[str, int]] = {}
    for size in sorted(by_size):
        pool = list(by_size[size])
        rng.shuffle(pool)
        target = picks_for_size_bucket(args.levels_per_size_factor, rng)
        chosen_count = min(target, len(pool))
        chosen = pool[:chosen_count]
        selected.extend(chosen)
        size_selection_summary[size] = {
            "available": len(pool),
            "target": target,
            "selected": chosen_count,
        }

    out_root = args.root / args.out_subdir
    out_levels = out_root / "levels"
    out_solutions = out_root / "solutions"
    out_manifest = out_root / "manifest.json"

    if out_root.exists():
        if args.overwrite:
            if not args.dry_run:
                shutil.rmtree(out_root)
        else:
            print(f"Error: output already exists: {out_root}. Use --overwrite.", file=sys.stderr)
            return 2

    if not args.dry_run:
        out_levels.mkdir(parents=True, exist_ok=True)
        out_solutions.mkdir(parents=True, exist_ok=True)

    final_levels: list[dict[str, object]] = []
    for index, candidate in enumerate(selected, start=1):
        if not args.dry_run:
            materialize_candidate(candidate.level_path, out_levels / f"{index}.level", args.copy_mode)
            materialize_candidate(candidate.solution_path, out_solutions / f"{index}.solution.json", args.copy_mode)
        final_levels.append(
            {
                "final_level_id": index,
                "size": candidate.size,
                "source_generator": candidate.generator,
                "source_level_id": candidate.source_level_id,
                "source_level_path": str(candidate.level_path),
                "source_solution_path": str(candidate.solution_path),
                "min_moves_to_exit": candidate.min_moves_to_exit,
                "min_direction_types_to_exit": candidate.min_direction_types_to_exit,
                "solution_steps": candidate.solution_steps,
                "level_hash": candidate.level_hash,
                "solution_hash": candidate.solution_hash,
            }
        )

    final_manifest: dict[str, object] = {
        "created_at": now_utc_iso(),
        "tool": "assemble_level_set.py",
        "root": str(args.root),
        "best_of_subdir": args.best_of_subdir,
        "out_subdir": args.out_subdir,
        "seed": seed,
        "levels_per_size_factor": args.levels_per_size_factor,
        "generator_filter": sorted(generator_filters) if generator_filters is not None else None,
        "copy_mode": args.copy_mode,
        "dry_run": args.dry_run,
        "skipped_counts": skipped_counts,
        "summary": {
            "candidate_count": len(candidates),
            "selected_count": len(selected),
            "size_bucket_count": len(by_size),
        },
        "size_selection": {str(size): data for size, data in sorted(size_selection_summary.items())},
        "levels": final_levels,
    }

    if not args.dry_run:
        out_manifest.write_text(json.dumps(final_manifest, indent=2), encoding="utf-8")

    print(
        f"Final assembly: candidates={len(candidates)}, selected={len(selected)}, "
        f"sizes={len(by_size)}, factor={args.levels_per_size_factor}, seed={seed}"
    )
    print(f"Skipped: missing_solution={skipped_counts['missing_solution']} parse_error={skipped_counts['parse_error']} non_square={skipped_counts['non_square']}")
    for size in sorted(size_selection_summary):
        row = size_selection_summary[size]
        print(f"  size {size}: available={row['available']} target={row['target']} selected={row['selected']}")
    if args.dry_run:
        print(f"Dry run: no files written. Planned output root: {out_root}")
    else:
        print(f"Wrote final set to {out_root} (levels={len(selected)})")

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
