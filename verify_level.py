#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import sensejump_core as core


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Verify a SenseJump program against a level. "
            "Can also validate an official private solution file."
        )
    )
    parser.add_argument("level", help="Level string or path to a .level file.")
    parser.add_argument(
        "attempt",
        nargs="?",
        help="Program string or path to a program file (optional if checking official solution only).",
    )
    parser.add_argument(
        "--solution-file",
        type=Path,
        default=None,
        help="Path to private .solution.json file to cross-check.",
    )
    parser.add_argument(
        "--answer-dir",
        type=Path,
        default=None,
        help="Directory containing private solution files named <level_stem>.solution.json.",
    )
    parser.add_argument(
        "--official-only",
        action="store_true",
        help="Skip verifying attempt and only verify official solution metadata/program.",
    )
    return parser


def _load_solution_payload(path: Path) -> dict[str, object]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"Could not read solution file {path}: {exc}") from exc
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Malformed JSON in solution file {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"Solution file {path} must contain a JSON object.")
    return payload


def _resolve_solution_path(
    provided_path: Path | None,
    answer_dir: Path | None,
    level_arg: str,
    level: core.Level,
) -> Path | None:
    if provided_path is not None:
        return provided_path
    if answer_dir is None:
        return None

    level_path = Path(level_arg)
    if level_path.is_file():
        stem = level_path.stem
    elif level.level_id:
        stem = str(level.level_id)
    else:
        return None

    return answer_dir / f"{stem}.solution.json"


def _verify_official_solution(level: core.Level, payload: dict[str, object]) -> tuple[bool, str]:
    solution_program = payload.get("solution_program")
    if not isinstance(solution_program, str) or not solution_program.strip():
        return False, "Official solution file is missing non-empty solution_program."

    claimed_level_hash = payload.get("level_hash")
    actual_level_hash = core.compute_level_hash(level)
    if claimed_level_hash is not None and claimed_level_hash != actual_level_hash:
        return (
            False,
            f"Official level_hash mismatch (expected {actual_level_hash}, got {claimed_level_hash}).",
        )

    try:
        program = core.parse_program_text(solution_program)
    except core.ProgramFormatError as exc:
        return False, f"Official solution_program is invalid: {exc}"

    if len(program) > level.program_limit:
        return (
            False,
            f"Official solution length {len(program)} exceeds program limit {level.program_limit}.",
        )

    program_hash = core.compute_program_hash(program)
    claimed_solution_hash = payload.get("solution_hash")
    if claimed_solution_hash is not None and claimed_solution_hash != program_hash:
        return (
            False,
            f"Official solution_hash mismatch (expected {program_hash}, got {claimed_solution_hash}).",
        )
    if level.solution_hash and level.solution_hash != program_hash:
        return (
            False,
            f"Level solhash mismatch (expected {level.solution_hash}, got {program_hash}).",
        )

    result = core.simulate_program(level, program, level.execution_limit)
    if result.outcome != "escape":
        return False, f"Official solution does not solve level (outcome={result.outcome})."

    claimed_steps = payload.get("solution_steps")
    if claimed_steps is not None:
        try:
            claimed_steps_int = int(claimed_steps)
        except (TypeError, ValueError):
            return False, f"Official solution_steps is invalid: {claimed_steps!r}."
        if claimed_steps_int != result.steps:
            return (
                False,
                f"Official solution_steps mismatch (expected {result.steps}, got {claimed_steps_int}).",
            )

    return True, f"Official solution verified in {result.steps} steps."


def main(argv: list[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        level_raw = core.read_text_arg(args.level)
        level = core.parse_level(level_raw)
    except (OSError, core.LevelFormatError) as exc:
        print(f"Error: {exc}")
        return 2

    if args.official_only and args.attempt:
        print("Error: --official-only cannot be used with an attempt argument.")
        return 2

    solution_path = _resolve_solution_path(args.solution_file, args.answer_dir, args.level, level)
    if args.official_only and solution_path is None:
        print("Error: --official-only requires --solution-file or --answer-dir.")
        return 2
    if args.attempt is None and solution_path is None:
        print("Error: provide an attempt and/or official solution source.")
        return 2

    all_ok = True

    if not args.official_only and args.attempt is not None:
        try:
            attempt_raw = core.read_text_arg(args.attempt)
        except OSError as exc:
            print(f"Error: {exc}")
            return 2
        ok, message, _program, _result = core.verify_program(level, attempt_raw)
        print(message)
        if not ok:
            all_ok = False

    if solution_path is not None:
        if not solution_path.is_file():
            print(f"Official solution file not found: {solution_path}")
            all_ok = False
        else:
            try:
                payload = _load_solution_payload(solution_path)
            except ValueError as exc:
                print(f"Error: {exc}")
                return 2
            ok, message = _verify_official_solution(level, payload)
            print(message)
            if not ok:
                all_ok = False

    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

