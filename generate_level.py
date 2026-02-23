#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

import sensejump_core as core


REJECT_CODE_ORDER = (
    "sr",
    "md",
    "pl",
    "ux",
    "mj",
    "ct",
    "ms",
    "js",
    "sb",
    "se",
    "ot",
    "ne",
    "rj",
    "rs",
    "tc",
    "np",
    "dn",
)


def update_progress_line(line: str, previous_width: int, enabled: bool = True) -> int:
    if not enabled:
        return 0
    padded = line
    if previous_width > len(line):
        padded += " " * (previous_width - len(line))
    sys.stdout.write("\r" + padded)
    sys.stdout.flush()
    return len(line)


def clear_progress_line(previous_width: int, enabled: bool = True) -> None:
    if not enabled or previous_width <= 0:
        return
    sys.stdout.write("\r" + (" " * previous_width) + "\r")
    sys.stdout.flush()


def format_reject_counts(reject_counts: dict[str, int]) -> str:
    if not reject_counts:
        return "-"
    parts: list[str] = []
    seen: set[str] = set()
    for code in REJECT_CODE_ORDER:
        count = reject_counts.get(code, 0)
        if count <= 0:
            continue
        parts.append(f"{code}={count}")
        seen.add(code)
    for code in sorted(reject_counts):
        if code in seen:
            continue
        count = reject_counts[code]
        if count <= 0:
            continue
        parts.append(f"{code}={count}")
    return " ".join(parts) if parts else "-"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Generate one SenseJump level and (optionally) write a private solution file."
        )
    )
    parser.add_argument("level_id", help="Level identifier (for id=... in the level file).")
    parser.add_argument("--width", type=int, default=11, help="Legacy base size input (default: 11).")
    parser.add_argument("--height", type=int, default=11, help="Legacy input, ignored (boards are square).")
    parser.add_argument(
        "--size",
        type=int,
        default=None,
        help="Square board size (overrides --width/--height).",
    )
    parser.add_argument(
        "--density",
        type=float,
        default=28.0,
        help="Blocked-cell density in percent (default: 28).",
    )
    parser.add_argument(
        "--solution-length",
        type=int,
        default=9,
        help="Target hidden solution length (default: 9).",
    )
    parser.add_argument(
        "--program-limit",
        type=int,
        default=14,
        help="Program length limit stored in the level (default: 14).",
    )
    parser.add_argument(
        "--execution-limit",
        type=int,
        default=420,
        help="Max instruction executions before timeout (default: 420).",
    )
    parser.add_argument(
        "--max-attempts",
        type=int,
        default=650,
        help="Max generation attempts before failing (0 = infinite, default: 650).",
    )
    parser.add_argument(
        "--max-straight-run",
        type=int,
        default=10,
        help=(
            "Reject generated levels when hidden solution has a straight run of this many "
            "or more moves in one direction (0 disables, default: 10)."
        ),
    )
    parser.add_argument(
        "--min-direction-types-to-exit",
        type=int,
        default=3,
        help=(
            "Minimum distinct movement directions (N/E/S/W) required for any movement-only "
            "escape path (1-4, default: 3)."
        ),
    )
    parser.add_argument(
        "--seal-unreachable",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "After generation, mark open cells unreachable from the start as blocked "
            "before writing outputs (use --no-seal-unreachable to disable, default: true)."
        ),
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="RNG seed for reproducible generation (default: random).",
    )
    parser.add_argument(
        "--level-out",
        type=Path,
        default=None,
        help="Path to write public .level output.",
    )
    parser.add_argument(
        "--solution-out",
        type=Path,
        default=None,
        help="Path to write private .solution.json output.",
    )
    parser.add_argument(
        "--print-solution",
        action="store_true",
        help="Print hidden solution text to stderr for local debugging.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show live generation progress and reject reason counts.",
    )
    return parser


def timestamp_now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def build_solution_payload(
    generated: core.GeneratedLevel,
    level_seed: int,
    density_percent: float,
    seal_unreachable: bool,
    sealed_unreachable_cells: int,
    options: core.GenerateOptions,
) -> dict[str, object]:
    return {
        "v": 1,
        "id": generated.level.level_id,
        "level_hash": generated.level_hash,
        "solution_hash": generated.level.solution_hash,
        "solution_program": generated.solution_text,
        "solution_steps": generated.solution_steps,
        "min_moves_to_exit": generated.min_moves_to_exit,
        "min_direction_types_to_exit": generated.min_direction_types_to_exit,
        "generator": {
            "seed": level_seed,
            "seal_unreachable": seal_unreachable,
            "sealed_unreachable_cells": sealed_unreachable_cells,
            "attempts_used": generated.attempts_used,
            "width": options.width,
            "height": options.height,
            "density_percent": density_percent,
            "target_solution_len": options.solution_length,
            "program_limit": options.program_limit,
            "execution_limit": options.execution_limit,
            "max_attempts": options.max_attempts,
            "max_straight_run": options.max_straight_run,
            "min_direction_types_to_exit_required": options.min_direction_types_to_exit,
        },
        "created_at": timestamp_now_utc(),
    }


def finalize_generated_level(generated: core.GeneratedLevel, seal_unreachable: bool) -> int:
    if not seal_unreachable:
        return 0
    sealed_unreachable_cells = core.seal_unreachable_cells(generated.level)
    if sealed_unreachable_cells <= 0:
        return 0
    generated.level_text = core.format_level(generated.level)
    generated.level_hash = core.compute_level_hash(generated.level)
    min_moves_to_exit = core.minimum_moves_to_exit(generated.level)
    if min_moves_to_exit is None:
        raise RuntimeError("Generated level has no movement-only path to exit after sealing unreachable cells.")
    min_direction_types_to_exit = core.minimum_distinct_directions_to_exit(generated.level)
    if min_direction_types_to_exit is None:
        raise RuntimeError("Generated level has no direction-subset path to exit after sealing unreachable cells.")
    generated.min_moves_to_exit = min_moves_to_exit
    generated.min_direction_types_to_exit = min_direction_types_to_exit
    return sealed_unreachable_cells


def main(argv: list[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.seed is None:
        args.seed = random.SystemRandom().randrange(0, 2**63)

    base_size = args.size if args.size is not None else args.width
    if base_size < 2:
        print("Error: square size must be >= 2.", file=sys.stderr)
        return 2
    if args.height != args.width and args.size is None:
        print(
            f"Info: forcing square board; using width={args.width} and ignoring height={args.height}.",
            file=sys.stderr,
        )
    program_limit = args.program_limit
    if args.solution_length > program_limit:
        print(
            f"Info: --solution-length {args.solution_length} exceeds --program-limit {program_limit}; "
            f"using program limit {args.solution_length}.",
            file=sys.stderr,
        )
        program_limit = args.solution_length

    density = args.density / 100.0
    options = core.GenerateOptions(
        width=base_size,
        height=base_size,
        density=density,
        solution_length=args.solution_length,
        program_limit=program_limit,
        execution_limit=args.execution_limit,
        max_attempts=args.max_attempts,
        max_straight_run=args.max_straight_run,
        min_direction_types_to_exit=args.min_direction_types_to_exit,
    )
    max_attempts_text = "inf" if options.max_attempts == 0 else str(options.max_attempts)
    show_live_progress = args.verbose and sys.stdout.isatty()
    progress_width = 0
    attempt_field_width = 3 if options.max_attempts == 0 else max(1, len(str(options.max_attempts)))
    reject_counts: dict[str, int] = {}

    if args.verbose:
        print(
            f"Level {args.level_id} constraints: "
            f"size={options.width}x{options.height}, "
            f"density={args.density:.1f}%, "
            f"target_sol={options.solution_length}, "
            f"plim={options.program_limit}, "
            f"elim={options.execution_limit}, "
            f"max_attempts={max_attempts_text}, "
            f"max_straight_run={options.max_straight_run}, "
            f"min_direction_types_to_exit={options.min_direction_types_to_exit}"
        )

    def progress_callback(attempt: int, max_attempts: int, status: str) -> None:
        nonlocal progress_width, attempt_field_width
        status_text = status
        if status.startswith("rejected:"):
            reject_code = status.split(":", 1)[1] or "??"
            reject_counts[reject_code] = reject_counts.get(reject_code, 0) + 1
            status_text = f"rejected({reject_code})"
        if not show_live_progress:
            return
        attempt_field_width = max(attempt_field_width, len(str(attempt)))
        attempt_text = str(attempt).rjust(attempt_field_width)
        max_text = "inf" if max_attempts == 0 else str(max_attempts).rjust(attempt_field_width)
        reject_suffix = f", {format_reject_counts(reject_counts)}" if reject_counts else ""
        progress_width = update_progress_line(
            f"Level {args.level_id}: attempts={attempt_text}/{max_text}, status={status_text}{reject_suffix}",
            progress_width,
            show_live_progress,
        )

    try:
        generated = core.generate_level(
            args.level_id,
            options,
            random.Random(args.seed),
            progress_callback=progress_callback if args.verbose else None,
        )
        sealed_unreachable_cells = finalize_generated_level(generated, args.seal_unreachable)
    except (ValueError, RuntimeError) as exc:
        clear_progress_line(progress_width, show_live_progress)
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    clear_progress_line(progress_width, show_live_progress)

    if args.verbose:
        print(
            f"Level {args.level_id}: ok "
            f"(attempts={generated.attempts_used}, solution_steps={generated.solution_steps}, "
            f"min_moves_to_exit={generated.min_moves_to_exit}, "
            f"min_direction_types_to_exit={generated.min_direction_types_to_exit}, "
            f"rejects={format_reject_counts(reject_counts)})"
        )

    if args.level_out:
        args.level_out.parent.mkdir(parents=True, exist_ok=True)
        args.level_out.write_text(generated.level_text + "\n", encoding="utf-8")

    if args.solution_out:
        payload = build_solution_payload(
            generated,
            args.seed,
            args.density,
            args.seal_unreachable,
            sealed_unreachable_cells,
            options,
        )
        args.solution_out.parent.mkdir(parents=True, exist_ok=True)
        args.solution_out.write_text(
            json.dumps(payload, indent=2, sort_keys=False) + "\n",
            encoding="utf-8",
        )

    if args.level_out is None:
        print(generated.level_text)
    else:
        print(f"Wrote level: {args.level_out}")
    if args.solution_out is not None:
        print(f"Wrote solution: {args.solution_out}")

    if args.print_solution:
        print(f"Hidden solution: {generated.solution_text}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
