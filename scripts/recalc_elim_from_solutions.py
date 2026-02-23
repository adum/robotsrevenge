#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import sensejump_core as core


def sort_key(path: Path) -> tuple[int, str]:
    stem = path.stem
    if stem.isdigit():
        return (0, f"{int(stem):08d}")
    return (1, stem)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Rewrite each .level file's elim to match solution_steps from the "
            "corresponding .solution.json file."
        )
    )
    parser.add_argument(
        "--levels-dir",
        type=Path,
        default=Path("levels"),
        help="Directory containing .level files (default: levels).",
    )
    parser.add_argument(
        "--solutions-dir",
        type=Path,
        default=Path("solutions"),
        help="Directory containing .solution.json files (default: solutions).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would change without rewriting files.",
    )
    return parser.parse_args(argv)


def load_solution_steps(solution_path: Path) -> int:
    try:
        payload = json.loads(solution_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{solution_path}: invalid JSON: {exc}") from exc

    steps = payload.get("solution_steps")
    if not isinstance(steps, int) or steps < 1:
        raise ValueError(
            f"{solution_path}: solution_steps must be a positive integer (got {steps!r})."
        )
    return steps


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    levels_dir: Path = args.levels_dir
    solutions_dir: Path = args.solutions_dir

    if not levels_dir.is_dir():
        print(f"Error: levels directory does not exist: {levels_dir}", file=sys.stderr)
        return 2
    if not solutions_dir.is_dir():
        print(f"Error: solutions directory does not exist: {solutions_dir}", file=sys.stderr)
        return 2

    level_paths = sorted(levels_dir.glob("*.level"), key=sort_key)
    if not level_paths:
        print(f"Error: no .level files found in {levels_dir}", file=sys.stderr)
        return 2

    updated = 0
    unchanged = 0

    for level_path in level_paths:
        solution_path = solutions_dir / f"{level_path.stem}.solution.json"
        if not solution_path.is_file():
            print(
                f"Error: missing solution file for {level_path.name}: {solution_path}",
                file=sys.stderr,
            )
            return 2

        steps = load_solution_steps(solution_path)
        level = core.parse_level(level_path.read_text(encoding="utf-8"))
        old_elim = level.execution_limit
        new_elim = max(1, steps)

        if old_elim == new_elim:
            unchanged += 1
            continue

        level.execution_limit = new_elim
        updated += 1
        print(f"{level_path}: elim {old_elim} -> {new_elim}")
        if not args.dry_run:
            level_path.write_text(core.format_level(level) + "\n", encoding="utf-8")

    mode = "dry-run" if args.dry_run else "rewrite"
    print(
        f"Done ({mode}): processed={len(level_paths)} updated={updated} unchanged={unchanged}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
