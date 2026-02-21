#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

import sensejump_core as core


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Generate one SenseJump level and (optionally) write a private solution file."
        )
    )
    parser.add_argument("level_id", help="Level identifier (for id=... in the level file).")
    parser.add_argument("--width", type=int, default=11, help="Board width (default: 11).")
    parser.add_argument("--height", type=int, default=11, help="Board height (default: 11).")
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
        help="Max generation attempts before failing (default: 650).",
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
    return parser


def timestamp_now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def build_solution_payload(
    generated: core.GeneratedLevel,
    level_seed: int,
    density_percent: float,
    options: core.GenerateOptions,
) -> dict[str, object]:
    return {
        "v": 1,
        "id": generated.level.level_id,
        "level_hash": generated.level_hash,
        "solution_hash": generated.level.solution_hash,
        "solution_program": generated.solution_text,
        "solution_steps": generated.solution_steps,
        "generator": {
            "seed": level_seed,
            "attempts_used": generated.attempts_used,
            "width": options.width,
            "height": options.height,
            "density_percent": density_percent,
            "target_solution_len": options.solution_length,
            "program_limit": options.program_limit,
            "execution_limit": options.execution_limit,
            "max_attempts": options.max_attempts,
            "max_straight_run": options.max_straight_run,
        },
        "created_at": timestamp_now_utc(),
    }


def main(argv: list[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.seed is None:
        args.seed = random.SystemRandom().randrange(0, 2**63)

    density = args.density / 100.0
    options = core.GenerateOptions(
        width=args.width,
        height=args.height,
        density=density,
        solution_length=args.solution_length,
        program_limit=args.program_limit,
        execution_limit=args.execution_limit,
        max_attempts=args.max_attempts,
        max_straight_run=args.max_straight_run,
    )

    try:
        generated = core.generate_level(args.level_id, options, random.Random(args.seed))
    except (ValueError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    if args.level_out:
        args.level_out.parent.mkdir(parents=True, exist_ok=True)
        args.level_out.write_text(generated.level_text + "\n", encoding="utf-8")

    if args.solution_out:
        payload = build_solution_payload(generated, args.seed, args.density, options)
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
