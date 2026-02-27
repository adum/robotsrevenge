#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import sensejump_core as core


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def level_id_from_path(path: Path) -> int | None:
    stem = path.name.split(".", 1)[0]
    if not stem.isdigit():
        return None
    return int(stem)


def find_generator_dirs(root: Path, filter_ids: set[str] | None) -> list[Path]:
    if not root.exists():
        return []
    dirs: list[Path] = []
    for path in sorted(root.iterdir()):
        if not path.is_dir():
            continue
        if filter_ids is not None and path.name not in filter_ids:
            continue
        run_dirs = [p for p in path.iterdir() if p.is_dir() and p.name.startswith("run_")]
        if run_dirs:
            dirs.append(path)
    return dirs


def candidate_score_tuple(
    metric: str,
    min_moves_to_exit: int | None,
    min_direction_types_to_exit: int | None,
    solution_steps: int | None,
) -> tuple:
    moves = -1 if min_moves_to_exit is None else int(min_moves_to_exit)
    dirs = -1 if min_direction_types_to_exit is None else int(min_direction_types_to_exit)
    steps = -1 if solution_steps is None else int(solution_steps)
    if metric == "solution_steps":
        return (steps, dirs, moves)
    if metric == "min_direction_types_to_exit":
        return (dirs, moves, steps)
    if metric == "combined":
        # Lexicographic combined objective:
        # 1) harder exit-direction requirement
        # 2) larger movement-only shortest path
        # 3) longer known solution trace
        return (dirs, moves, steps)
    return (moves, dirs, steps)


def load_invalid_map(report_path: Path | None) -> dict[tuple[str, str, int], list[str]]:
    invalid: dict[tuple[str, str, int], list[str]] = {}
    if report_path is None:
        return invalid
    raw = json.loads(report_path.read_text(encoding="utf-8"))
    runs = raw.get("runs", [])
    if not isinstance(runs, list):
        return invalid
    for run in runs:
        if not isinstance(run, dict):
            continue
        generator = run.get("generator")
        run_id = run.get("run_id")
        candidates = run.get("candidates", [])
        if not isinstance(generator, str) or not isinstance(run_id, str) or not isinstance(candidates, list):
            continue
        for item in candidates:
            if not isinstance(item, dict):
                continue
            if item.get("valid", True):
                continue
            level_id = item.get("level_id")
            reasons = item.get("reasons", [])
            if isinstance(level_id, int):
                invalid[(generator, run_id, level_id)] = reasons if isinstance(reasons, list) else []
    return invalid


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Reduce generated candidates to one best candidate per level per generator, "
            "using outputs from generated/<generator>/run_XXX."
        )
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=ROOT_DIR / "generated",
        help="Generated root directory (default: generated).",
    )
    parser.add_argument(
        "--generator",
        action="append",
        default=[],
        help="Filter generator ID (can repeat).",
    )
    parser.add_argument(
        "--metric",
        choices=("min_moves_to_exit", "solution_steps", "min_direction_types_to_exit", "combined"),
        default="min_moves_to_exit",
        help="Primary selection metric (default: min_moves_to_exit).",
    )
    parser.add_argument(
        "--check-report",
        type=Path,
        default=None,
        help="Optional check report (from check_generated_levels.py) to exclude invalid candidates.",
    )
    parser.add_argument(
        "--best-of-subdir",
        type=str,
        default="best_of",
        help="Output subdirectory name inside each generator dir (default: best_of).",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing best-of output directories.",
    )
    parser.add_argument(
        "--copy-mode",
        choices=("copy", "hardlink"),
        default="copy",
        help="How selected files are materialized (default: copy).",
    )
    parser.add_argument("--dry-run", action="store_true", help="Do not write/copy files, only print summary.")
    return parser


def main(argv: list[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    filter_ids = set(args.generator) if args.generator else None
    invalid_map = load_invalid_map(args.check_report)
    generator_dirs = find_generator_dirs(args.root, filter_ids)
    if not generator_dirs:
        print(f"No generator directories found under {args.root}", file=sys.stderr)
        return 1

    for generator_dir in generator_dirs:
        generator_id = generator_dir.name
        run_dirs = sorted(path for path in generator_dir.iterdir() if path.is_dir() and path.name.startswith("run_"))
        by_level: dict[int, list[dict[str, object]]] = {}

        for run_dir in run_dirs:
            run_id = run_dir.name
            levels_dir = run_dir / "levels"
            solutions_dir = run_dir / "solutions"
            if not levels_dir.is_dir() or not solutions_dir.is_dir():
                continue

            for level_path in sorted(levels_dir.glob("*.level")):
                level_id = level_id_from_path(level_path)
                if level_id is None:
                    continue
                solution_path = solutions_dir / f"{level_id}.solution.json"
                if not solution_path.exists():
                    continue
                if (generator_id, run_id, level_id) in invalid_map:
                    continue

                try:
                    level = core.parse_level(level_path.read_text(encoding="utf-8"))
                    solution_data = json.loads(solution_path.read_text(encoding="utf-8"))
                except Exception:  # noqa: BLE001
                    continue

                min_moves = solution_data.get("min_moves_to_exit")
                min_dirs = solution_data.get("min_direction_types_to_exit")
                solution_steps = solution_data.get("solution_steps")
                if min_moves is None:
                    min_moves = core.minimum_moves_to_exit(level)
                if min_dirs is None:
                    min_dirs = core.minimum_distinct_directions_to_exit(level)

                score_tuple = candidate_score_tuple(args.metric, min_moves, min_dirs, solution_steps)
                by_level.setdefault(level_id, []).append(
                    {
                        "generator": generator_id,
                        "run_id": run_id,
                        "level_id": level_id,
                        "level_path": level_path,
                        "solution_path": solution_path,
                        "score_tuple": score_tuple,
                        "min_moves_to_exit": min_moves,
                        "min_direction_types_to_exit": min_dirs,
                        "solution_steps": solution_steps,
                        "level_hash": solution_data.get("level_hash"),
                        "solution_hash": solution_data.get("solution_hash"),
                    }
                )

        best_root = generator_dir / args.best_of_subdir
        best_levels_dir = best_root / "levels"
        best_solutions_dir = best_root / "solutions"
        best_manifest_path = best_root / "manifest.json"

        if best_root.exists():
            if args.overwrite:
                shutil.rmtree(best_root)
            else:
                print(
                    f"Error: output already exists for {generator_id}: {best_root}. "
                    "Use --overwrite to replace.",
                    file=sys.stderr,
                )
                return 2

        if not args.dry_run:
            best_levels_dir.mkdir(parents=True, exist_ok=True)
            best_solutions_dir.mkdir(parents=True, exist_ok=True)

        selected_levels: list[dict[str, object]] = []
        missing_level_ids: list[int] = []

        for level_id in sorted(by_level):
            candidates = by_level[level_id]
            if not candidates:
                missing_level_ids.append(level_id)
                continue

            best = max(candidates, key=lambda item: item["score_tuple"])
            selected_levels.append(
                {
                    "level_id": level_id,
                    "selected_from_run": best["run_id"],
                    "score_tuple": list(best["score_tuple"]),
                    "min_moves_to_exit": best["min_moves_to_exit"],
                    "min_direction_types_to_exit": best["min_direction_types_to_exit"],
                    "solution_steps": best["solution_steps"],
                    "level_hash": best["level_hash"],
                    "solution_hash": best["solution_hash"],
                    "candidate_count": len(candidates),
                    "candidates": [
                        {
                            "run_id": candidate["run_id"],
                            "score_tuple": list(candidate["score_tuple"]),
                            "min_moves_to_exit": candidate["min_moves_to_exit"],
                            "min_direction_types_to_exit": candidate["min_direction_types_to_exit"],
                            "solution_steps": candidate["solution_steps"],
                        }
                        for candidate in sorted(candidates, key=lambda item: item["score_tuple"], reverse=True)
                    ],
                }
            )

            if args.dry_run:
                continue

            dst_level = best_levels_dir / f"{level_id}.level"
            dst_solution = best_solutions_dir / f"{level_id}.solution.json"
            src_level = Path(best["level_path"])
            src_solution = Path(best["solution_path"])
            if args.copy_mode == "hardlink":
                dst_level.hardlink_to(src_level)
                dst_solution.hardlink_to(src_solution)
            else:
                shutil.copy2(src_level, dst_level)
                shutil.copy2(src_solution, dst_solution)

        best_manifest = {
            "created_at": now_utc_iso(),
            "tool": "reduce_generator_candidates.py",
            "generator": generator_id,
            "metric": args.metric,
            "source_run_count": len(run_dirs),
            "selected_count": len(selected_levels),
            "missing_level_ids": missing_level_ids,
            "levels": selected_levels,
        }

        if not args.dry_run:
            best_manifest_path.write_text(json.dumps(best_manifest, indent=2), encoding="utf-8")

        print(
            f"{generator_id}: selected {len(selected_levels)} levels from {len(run_dirs)} runs "
            f"using metric={args.metric}"
        )
        if not args.dry_run:
            print(f"  output: {best_root}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
