#!/usr/bin/env python3
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

import sensejump_core as core


def solver_command(solver_path: str) -> list[str]:
    solver_file = Path(solver_path)
    if solver_file.suffix == ".py":
        return [sys.executable, str(solver_file)]
    return [str(solver_file)]


def level_sort_key(path: Path) -> tuple[int, str]:
    try:
        return (int(path.stem), path.name)
    except ValueError:
        return (sys.maxsize, path.name)


def level_number(path: Path) -> Optional[int]:
    try:
        return int(path.stem)
    except ValueError:
        return None


def extract_solution_text(stdout: str) -> str:
    lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    if not lines:
        return ""
    return lines[-1]


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Meta solver: run a solver script over all SenseJump levels in a directory."
    )
    parser.add_argument(
        "solver",
        nargs="?",
        default="solve_level.py",
        help="Path to solver program (default: solve_level.py).",
    )
    parser.add_argument(
        "--levels-dir",
        default="levels",
        help="Directory containing .level files (default: levels).",
    )
    parser.add_argument("--start", type=int, default=1, help="Starting numeric level (default: 1).")
    parser.add_argument("--end", type=int, default=None, help="Ending numeric level.")
    parser.add_argument(
        "--stdin",
        action="store_true",
        help="Pass level content on stdin instead of a level file path argument.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=0.0,
        help="Per-level solver timeout in seconds, <=0 means no timeout (default: 0 = no timeout).",
    )
    parser.add_argument(
        "--continue-on-fail",
        action="store_true",
        help="Continue after failures; by default stop at first failure.",
    )
    parser.add_argument(
        "--show-stderr",
        action="store_true",
        help="Print solver stderr even on pass.",
    )
    args, solver_extra_args = parser.parse_known_args(argv)
    if solver_extra_args and solver_extra_args[0] == "--":
        solver_extra_args = solver_extra_args[1:]

    levels_dir = Path(args.levels_dir)
    if not levels_dir.is_dir():
        print(f"Error: levels directory not found: {levels_dir}")
        return 2

    level_files = sorted(
        [path for path in levels_dir.iterdir() if path.is_file() and path.suffix == ".level"],
        key=level_sort_key,
    )
    if args.end is not None:
        level_files = [
            path
            for path in level_files
            if level_number(path) is not None and args.start <= level_number(path) <= args.end
        ]
    else:
        level_files = [
            path for path in level_files if level_number(path) is not None and level_number(path) >= args.start
        ]

    if not level_files:
        print(f"No numeric .level files found between {args.start} and {args.end or 'end'}.")
        return 1

    solver_base = solver_command(args.solver)
    print(
        f"Running solver over {len(level_files)} levels "
        f"({level_files[0].stem}..{level_files[-1].stem}) using {' '.join(solver_base)}"
    )
    if solver_extra_args:
        print(f"Forwarding solver args: {' '.join(solver_extra_args)}")

    solved_count = 0
    failed_count = 0

    for level_path in level_files:
        try:
            level_content = level_path.read_text(encoding="utf-8").strip()
            level = core.parse_level(level_content)
        except (OSError, core.LevelFormatError) as exc:
            print(f"Level {level_path.stem}: ERROR parsing level: {exc}")
            failed_count += 1
            if not args.continue_on_fail:
                break
            continue

        label = (
            f"Level {level_path.stem} "
            f"({level.width}x{level.height}, plim={level.program_limit}, elim={level.execution_limit})"
        )
        print(f"{label}: ", end="", flush=True)

        run_cmd = solver_base + solver_extra_args
        if args.stdin:
            cmd = run_cmd
        else:
            cmd = run_cmd + [str(level_path)]

        start = time.perf_counter()
        try:
            timeout_value = None if args.timeout <= 0 else args.timeout
            process = subprocess.run(
                cmd,
                input=level_content if args.stdin else None,
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout_value,
            )
        except subprocess.TimeoutExpired:
            elapsed = time.perf_counter() - start
            print(f"TIMEOUT ({elapsed:.3f}s)")
            failed_count += 1
            if not args.continue_on_fail:
                break
            continue
        except OSError as exc:
            elapsed = time.perf_counter() - start
            print(f"ERROR ({elapsed:.3f}s)")
            print(f"  Could not launch solver: {exc}")
            failed_count += 1
            if not args.continue_on_fail:
                break
            continue

        elapsed = time.perf_counter() - start
        solution_text = extract_solution_text(process.stdout)
        if not solution_text:
            print(f"FAIL ({elapsed:.3f}s)")
            print("  Error: solver produced no output")
            if process.stderr.strip():
                print(f"  Solver stderr: {process.stderr.strip()}")
            failed_count += 1
            if not args.continue_on_fail:
                break
            continue
        if solution_text == "No solution found":
            print(f"FAIL ({elapsed:.3f}s)")
            print("  Error: solver reported no solution")
            if process.stderr.strip():
                print(f"  Solver stderr: {process.stderr.strip()}")
            failed_count += 1
            if not args.continue_on_fail:
                break
            continue

        ok, message, program, result = core.verify_program(level, solution_text)
        if ok and program is not None and result is not None:
            solved_count += 1
            print(f"PASS ({elapsed:.3f}s, len={len(program)}, steps={result.steps})")
            if args.show_stderr and process.stderr.strip():
                print(f"  Solver stderr: {process.stderr.strip()}")
            continue

        print(f"FAIL ({elapsed:.3f}s)")
        print(f"  Error: {message}")
        print(f"  Candidate: {solution_text}")
        if process.stderr.strip():
            print(f"  Solver stderr: {process.stderr.strip()}")
        failed_count += 1
        if not args.continue_on_fail:
            break

    print(
        f"Summary: solved={solved_count} failed={failed_count} "
        f"attempted={solved_count + failed_count}"
    )
    return 0 if failed_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
