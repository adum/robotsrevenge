#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import sensejump_core as core


REJECT_REASON_NAMES: dict[str, str] = {
    "ms": "missing_solution_file",
    "lp": "level_parse_error",
    "sp": "missing_solution_program",
    "sj": "solution_json_parse_error",
    "hs": "solution_hash_mismatch",
    "hl": "level_hash_mismatch",
    "se": "provided_solution_does_not_escape",
    "mj": "meaningless_jump_instruction",
    "mm": "min_moves_to_exit_below_threshold",
    "md": "min_direction_types_to_exit_below_threshold",
    "ss": "solution_steps_below_threshold",
    "ed": "easy_two_direction_escape_found",
    "cp": "cpp_short_solution_found",
    "ce": "cpp_short_solver_error",
}


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def level_id_from_path(path: Path) -> int | None:
    stem = path.name.split(".", 1)[0]
    if not stem.isdigit():
        return None
    return int(stem)


def find_run_dirs(root: Path, generator_filters: set[str] | None, run_filters: set[str] | None) -> list[Path]:
    run_dirs: list[Path] = []
    if not root.exists():
        return run_dirs

    for generator_dir in sorted(path for path in root.iterdir() if path.is_dir()):
        if generator_filters is not None and generator_dir.name not in generator_filters:
            continue
        for run_dir in sorted(path for path in generator_dir.iterdir() if path.is_dir() and path.name.startswith("run_")):
            if run_filters is not None and run_dir.name not in run_filters:
                continue
            levels_dir = run_dir / "levels"
            solutions_dir = run_dir / "solutions"
            if levels_dir.is_dir() and solutions_dir.is_dir():
                run_dirs.append(run_dir)
    return run_dirs


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Check generated candidates and optionally delete invalid ones. "
            "Works on generated/<generator>/run_XXX directories."
        )
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=ROOT_DIR / "generated",
        help="Generated root folder (default: generated).",
    )
    parser.add_argument(
        "--generator",
        action="append",
        default=[],
        help="Filter generator ID (can repeat).",
    )
    parser.add_argument(
        "--run-id",
        action="append",
        default=[],
        help="Filter run ID such as run_001 (can repeat).",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="Report output path (default: <root>/check_report.json).",
    )

    parser.add_argument(
        "--verify-solution",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Verify provided solution actually escapes (default: on).",
    )
    parser.add_argument(
        "--verify-solution-hash",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Verify solution hash in JSON matches program text (default: on).",
    )
    parser.add_argument(
        "--verify-level-hash",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Verify level hash in solution JSON matches parsed level (default: off).",
    )

    parser.add_argument("--min-moves-to-exit", type=int, default=0, help="Reject levels with min_moves_to_exit below this.")
    parser.add_argument(
        "--min-direction-types-to-exit",
        type=int,
        default=0,
        help="Reject levels with min_direction_types_to_exit below this.",
    )
    parser.add_argument("--min-solution-steps", type=int, default=0, help="Reject levels with solution_steps below this.")
    parser.add_argument(
        "--reject-easy-two-direction",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Reject if an easy two-direction escape program exists.",
    )
    parser.add_argument(
        "--reject-meaningless-jump",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Reject if provided solution has a meaningless jump instruction.",
    )
    parser.add_argument(
        "--reject-cpp-short-solution-max-depth",
        type=int,
        default=0,
        help=(
            "Reject levels where the C++ brute solver finds a solution at or below "
            "this depth. 0 disables this check (default: 0)."
        ),
    )
    parser.add_argument(
        "--cpp-short-solver-path",
        type=Path,
        default=ROOT_DIR / "scripts" / "solve_level_cpp",
        help="Path to compiled C++ brute solver binary (default: scripts/solve_level_cpp).",
    )
    parser.add_argument(
        "--cpp-short-solver-timeout",
        type=float,
        default=0.0,
        help="Per-level timeout in seconds for C++ short-solver check. 0 disables timeout (default: 0).",
    )

    parser.add_argument(
        "--enforce",
        action="store_true",
        help="Enable enforcement mode. Without this flag, script only reports.",
    )
    parser.add_argument(
        "--delete-invalid",
        action="store_true",
        help="When enforcing, delete invalid level+solution files.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Do not delete files; report only planned deletes.")
    return parser


def has_cpp_short_solution(
    level_text: str,
    solver_path: Path,
    max_depth: int,
    timeout_seconds: float,
) -> tuple[bool | None, str | None]:
    try:
        completed = subprocess.run(
            [str(solver_path), "--min-depth", "1", "--max-depth", str(max_depth)],
            input=level_text,
            text=True,
            capture_output=True,
            check=False,
            timeout=(timeout_seconds if timeout_seconds > 0 else None),
        )
    except subprocess.TimeoutExpired:
        return None, "timeout"
    except Exception:  # noqa: BLE001
        return None, "invoke"

    if completed.returncode == 0:
        return True, None
    if completed.returncode == 1:
        return False, None
    return None, "rc"


def ensure_cpp_short_solver_binary(requested_path: Path, parser: argparse.ArgumentParser) -> Path:
    """
    Ensures a runnable C++ short-solver binary exists at requested_path.
    If missing, attempts to build from sibling .cpp source.
    """
    solver_path = requested_path
    if solver_path.exists() and solver_path.is_file() and os.access(solver_path, os.X_OK):
        return solver_path

    source_path = solver_path.with_suffix(".cpp")
    if not source_path.exists() or not source_path.is_file():
        parser.error(f"--cpp-short-solver-path not found or not executable: {solver_path}")

    compiler = shutil.which("g++")
    if compiler is None:
        parser.error(
            f"--cpp-short-solver-path is missing ({solver_path}) and g++ is not available to build from {source_path}."
        )

    try:
        source_mtime = source_path.stat().st_mtime
        binary_mtime = solver_path.stat().st_mtime if solver_path.exists() else 0.0
        needs_build = (not solver_path.exists()) or source_mtime > binary_mtime or not os.access(solver_path, os.X_OK)
    except OSError:
        needs_build = True

    if needs_build:
        build = subprocess.run(
            [compiler, "-O3", "-std=c++17", str(source_path), "-o", str(solver_path)],
            cwd=ROOT_DIR,
            capture_output=True,
            text=True,
            check=False,
        )
        if build.returncode != 0:
            stderr_text = (build.stderr or "").strip()
            parser.error(
                f"Failed to build C++ solver from {source_path} -> {solver_path}. "
                f"Compiler output: {stderr_text or 'unknown error'}"
            )

    if not solver_path.exists() or not solver_path.is_file() or not os.access(solver_path, os.X_OK):
        parser.error(f"--cpp-short-solver-path is not executable after build: {solver_path}")
    return solver_path


def main(argv: list[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.reject_cpp_short_solution_max_depth < 0:
        parser.error("--reject-cpp-short-solution-max-depth must be >= 0.")
    if args.cpp_short_solver_timeout < 0:
        parser.error("--cpp-short-solver-timeout must be >= 0.")

    generator_filters = set(args.generator) if args.generator else None
    run_filters = set(args.run_id) if args.run_id else None
    run_dirs = find_run_dirs(args.root, generator_filters, run_filters)

    report_path = args.report if args.report is not None else (args.root / "check_report.json")
    report_path.parent.mkdir(parents=True, exist_ok=True)

    if not run_dirs:
        print(f"No run directories found under {args.root}", file=sys.stderr)
        return 1

    cpp_short_solver_path: Path | None = None
    if args.reject_cpp_short_solution_max_depth > 0:
        cpp_short_solver_path = args.cpp_short_solver_path
        if not cpp_short_solver_path.is_absolute():
            cpp_short_solver_path = (ROOT_DIR / cpp_short_solver_path).resolve()
        cpp_short_solver_path = ensure_cpp_short_solver_binary(cpp_short_solver_path, parser)

    report: dict[str, object] = {
        "created_at": now_utc_iso(),
        "tool": "check_generated_levels.py",
        "root": str(args.root),
        "filters": {
            "generator": sorted(generator_filters) if generator_filters is not None else None,
            "run_id": sorted(run_filters) if run_filters is not None else None,
        },
        "checks": {
            "verify_solution": args.verify_solution,
            "verify_solution_hash": args.verify_solution_hash,
            "verify_level_hash": args.verify_level_hash,
            "min_moves_to_exit": args.min_moves_to_exit,
            "min_direction_types_to_exit": args.min_direction_types_to_exit,
            "min_solution_steps": args.min_solution_steps,
            "reject_easy_two_direction": args.reject_easy_two_direction,
            "reject_meaningless_jump": args.reject_meaningless_jump,
            "reject_cpp_short_solution_max_depth": args.reject_cpp_short_solution_max_depth,
            "cpp_short_solver_path": str(cpp_short_solver_path) if cpp_short_solver_path is not None else None,
            "cpp_short_solver_timeout": args.cpp_short_solver_timeout,
            "enforce": args.enforce,
            "delete_invalid": args.delete_invalid,
            "dry_run": args.dry_run,
        },
        "runs": [],
        "summary": {},
    }

    total_candidates = 0
    total_valid = 0
    total_invalid = 0
    reason_counts: dict[str, int] = {}
    deleted_count = 0

    for run_dir in run_dirs:
        generator_id = run_dir.parent.name
        run_id = run_dir.name
        levels_dir = run_dir / "levels"
        solutions_dir = run_dir / "solutions"
        level_files = sorted(levels_dir.glob("*.level"), key=lambda p: (level_id_from_path(p) is None, level_id_from_path(p), p.name))

        run_result: dict[str, object] = {
            "generator": generator_id,
            "run_id": run_id,
            "path": str(run_dir),
            "candidates": [],
        }

        for level_path in level_files:
            level_id = level_id_from_path(level_path)
            if level_id is None:
                continue
            solution_path = solutions_dir / f"{level_id}.solution.json"
            total_candidates += 1

            reasons: list[str] = []
            metrics: dict[str, object] = {}

            level_obj = None
            solution_data = None
            program_text = None
            program = None
            level_raw = None

            if not solution_path.exists():
                reasons.append("ms")
            try:
                level_raw = level_path.read_text(encoding="utf-8")
                level_obj = core.parse_level(level_raw)
                metrics["width"] = level_obj.width
                metrics["height"] = level_obj.height
                metrics["program_limit"] = level_obj.program_limit
                metrics["execution_limit"] = level_obj.execution_limit
                metrics["density_percent"] = round(
                    100.0 * core.block_count(level_obj.board) / float(max(1, level_obj.width * level_obj.height)),
                    2,
                )
            except Exception:  # noqa: BLE001
                reasons.append("lp")

            if solution_path.exists():
                try:
                    solution_data = json.loads(solution_path.read_text(encoding="utf-8"))
                    program_text = str(solution_data.get("solution_program", ""))
                    if not program_text:
                        reasons.append("sp")
                    else:
                        program = core.parse_program_text(program_text)
                    metrics["solution_steps"] = solution_data.get("solution_steps")
                    metrics["solution_hash"] = solution_data.get("solution_hash")
                    metrics["level_hash"] = solution_data.get("level_hash")
                    metrics["min_moves_to_exit"] = solution_data.get("min_moves_to_exit")
                    metrics["min_direction_types_to_exit"] = solution_data.get("min_direction_types_to_exit")
                except Exception:  # noqa: BLE001
                    reasons.append("sj")

            if args.verify_solution_hash and solution_data is not None and program is not None:
                expected_hash = core.compute_program_hash(program)
                actual_hash = solution_data.get("solution_hash")
                if actual_hash is not None and expected_hash != actual_hash:
                    reasons.append("hs")

            if args.verify_level_hash and level_obj is not None and solution_data is not None:
                expected_level_hash = core.compute_level_hash(level_obj)
                actual_level_hash = solution_data.get("level_hash")
                if actual_level_hash is not None and expected_level_hash != actual_level_hash:
                    reasons.append("hl")

            if args.verify_solution and level_obj is not None and program is not None:
                run_result_obj = core.simulate_program(level_obj, program, level_obj.execution_limit)
                metrics["sim_outcome"] = run_result_obj.outcome
                metrics["sim_steps"] = run_result_obj.steps
                if run_result_obj.outcome != "escape":
                    reasons.append("se")

            if args.reject_meaningless_jump and program is not None:
                if core.has_meaningless_jump_instruction(program):
                    reasons.append("mj")

            min_moves = metrics.get("min_moves_to_exit")
            if min_moves is None and level_obj is not None:
                min_moves = core.minimum_moves_to_exit(level_obj)
                metrics["min_moves_to_exit"] = min_moves
            if args.min_moves_to_exit > 0:
                if min_moves is None or int(min_moves) < args.min_moves_to_exit:
                    reasons.append("mm")

            min_dirs = metrics.get("min_direction_types_to_exit")
            if min_dirs is None and level_obj is not None:
                min_dirs = core.minimum_distinct_directions_to_exit(level_obj)
                metrics["min_direction_types_to_exit"] = min_dirs
            if args.min_direction_types_to_exit > 0:
                if min_dirs is None or int(min_dirs) < args.min_direction_types_to_exit:
                    reasons.append("md")

            if args.min_solution_steps > 0:
                steps = metrics.get("solution_steps")
                if steps is None:
                    steps = metrics.get("sim_steps")
                if steps is None or int(steps) < args.min_solution_steps:
                    reasons.append("ss")

            if args.reject_easy_two_direction and level_obj is not None:
                if core.has_easy_two_direction_program(level_obj):
                    reasons.append("ed")
            if (
                args.reject_cpp_short_solution_max_depth > 0
                and cpp_short_solver_path is not None
                and level_raw is not None
                and level_obj is not None
            ):
                short_found, error = has_cpp_short_solution(
                    level_raw,
                    cpp_short_solver_path,
                    args.reject_cpp_short_solution_max_depth,
                    args.cpp_short_solver_timeout,
                )
                metrics["cpp_short_check_max_depth"] = args.reject_cpp_short_solution_max_depth
                if short_found is True:
                    metrics["cpp_short_solution_found"] = True
                    reasons.append("cp")
                elif short_found is False:
                    metrics["cpp_short_solution_found"] = False
                else:
                    metrics["cpp_short_solver_error"] = error
                    reasons.append("ce")

            is_valid = len(reasons) == 0
            if is_valid:
                total_valid += 1
            else:
                total_invalid += 1
                for reason in sorted(set(reasons)):
                    reason_counts[reason] = reason_counts.get(reason, 0) + 1

                if args.enforce and args.delete_invalid:
                    if args.dry_run:
                        pass
                    else:
                        if level_path.exists():
                            os.remove(level_path)
                        if solution_path.exists():
                            os.remove(solution_path)
                    deleted_count += 1

            run_result["candidates"].append(
                {
                    "level_id": level_id,
                    "level_path": str(level_path),
                    "solution_path": str(solution_path),
                    "valid": is_valid,
                    "reasons": sorted(set(reasons)),
                    "metrics": metrics,
                    "deleted": bool(args.enforce and args.delete_invalid and not is_valid),
                }
            )

        report["runs"].append(run_result)

    report["summary"] = {
        "total_runs": len(run_dirs),
        "total_candidates": total_candidates,
        "valid": total_valid,
        "invalid": total_invalid,
        "deleted": deleted_count if args.enforce and args.delete_invalid else 0,
        "reason_counts": reason_counts,
    }
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(
        f"Checked {total_candidates} candidates across {len(run_dirs)} runs. "
        f"valid={total_valid}, invalid={total_invalid}, report={report_path}"
    )
    if reason_counts:
        summary_bits = []
        for code, count in sorted(reason_counts.items(), key=lambda item: (-item[1], item[0])):
            reason_name = REJECT_REASON_NAMES.get(code, "unknown_reason")
            summary_bits.append(f"{code}({reason_name})={count}")
        print("Reject reasons: " + " ".join(summary_bits))
    else:
        print("Reject reasons: none")
    if args.enforce and args.delete_invalid:
        if args.dry_run:
            print(f"Dry run: would delete {deleted_count} invalid candidates.")
        else:
            print(f"Deleted {deleted_count} invalid candidates.")

    return 0 if total_invalid == 0 or not args.enforce else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
