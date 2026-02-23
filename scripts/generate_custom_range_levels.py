#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

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


def timestamp_now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def clamp_int(value: int, min_value: int, max_value: int) -> int:
    return max(min_value, min(max_value, value))


def clamp_float(value: float, min_value: float, max_value: float) -> float:
    return max(min_value, min(max_value, value))


def smoothstep(t: float) -> float:
    t = clamp_float(t, 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


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


def apply_final_execution_limit(generated: core.GeneratedLevel, elim_from_solution_steps: bool) -> None:
    if not elim_from_solution_steps:
        return
    target_elim = max(1, generated.solution_steps)
    if generated.level.execution_limit == target_elim:
        return
    generated.level.execution_limit = target_elim
    generated.level_text = core.format_level(generated.level)
    generated.level_hash = core.compute_level_hash(generated.level)


def build_solution_payload(
    generated: core.GeneratedLevel,
    level_seed: int,
    options: core.GenerateOptions,
    level_index: int,
    level_count: int,
    size: int,
    density_percent: float,
    target_solution_len: int,
    seal_unreachable: bool,
    sealed_unreachable_cells: int,
    elim_from_solution_steps: bool,
    series_config: dict[str, object],
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
            "mode": "custom_range_oscillation",
            "seed": level_seed,
            "attempts_used": generated.attempts_used,
            "level_index": level_index,
            "level_count": level_count,
            "series_config": series_config,
            "width": size,
            "height": size,
            "density_percent": round(density_percent, 2),
            "target_solution_len": target_solution_len,
            "program_limit": target_solution_len,
            "execution_limit": generated.level.execution_limit,
            "generation_execution_limit": options.execution_limit,
            "max_attempts": options.max_attempts,
            "max_straight_run": options.max_straight_run,
            "min_direction_types_to_exit_required": options.min_direction_types_to_exit,
            "seal_unreachable": seal_unreachable,
            "sealed_unreachable_cells": sealed_unreachable_cells,
            "elim_from_solution_steps": elim_from_solution_steps,
        },
        "created_at": timestamp_now_utc(),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Generate a custom level range with smooth square-size growth and oscillating "
            "density/program-length bands."
        )
    )
    parser.add_argument("--start-level", type=int, default=101, help="First level number (default: 101).")
    parser.add_argument("--end-level", type=int, default=200, help="Last level number (default: 200).")
    parser.add_argument("--out-dir", type=Path, default=Path("levels"), help="Output .level directory.")
    parser.add_argument(
        "--solution-dir",
        type=Path,
        default=Path("solutions"),
        help="Output .solution.json directory.",
    )
    parser.add_argument("--min-size", type=int, default=50, help="Minimum square size (default: 50).")
    parser.add_argument("--max-size", type=int, default=300, help="Maximum square size (default: 300).")
    parser.add_argument("--min-density", type=float, default=20.0, help="Minimum density percent (default: 20).")
    parser.add_argument("--max-density", type=float, default=50.0, help="Maximum density percent (default: 50).")
    parser.add_argument(
        "--density-cycles",
        type=float,
        default=3.0,
        help="Density oscillation cycles over the full range (default: 3.0).",
    )
    parser.add_argument(
        "--density-phase",
        type=float,
        default=0.0,
        help="Density phase offset in radians (default: 0.0).",
    )
    parser.add_argument(
        "--min-solution-length",
        type=int,
        default=20,
        help="Minimum hidden solution/program length (default: 20).",
    )
    parser.add_argument(
        "--max-solution-length",
        type=int,
        default=50,
        help="Maximum hidden solution/program length (default: 50).",
    )
    parser.add_argument(
        "--solution-cycles",
        type=float,
        default=2.0,
        help="Program-length oscillation cycles over the full range (default: 2.0).",
    )
    parser.add_argument(
        "--solution-phase",
        type=float,
        default=math.pi / 5.0,
        help="Program-length phase offset in radians (default: pi/5).",
    )
    parser.add_argument(
        "--generation-execution-limit",
        type=int,
        default=20000,
        help="Execution limit used during generation search (default: 20000).",
    )
    parser.add_argument(
        "--max-attempts",
        type=int,
        default=0,
        help="Max attempts per level (0 = infinite, default: 0).",
    )
    parser.add_argument(
        "--max-straight-run",
        type=int,
        default=10,
        help="Reject hidden solutions with straight runs >= this value (default: 10).",
    )
    parser.add_argument(
        "--min-direction-types-to-exit",
        type=int,
        default=3,
        help="Minimum distinct movement directions required to escape (1-4, default: 3).",
    )
    parser.add_argument(
        "--seal-unreachable",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Mark cells unreachable from start as blocked before writing outputs "
            "(use --no-seal-unreachable to disable, default: true)."
        ),
    )
    parser.add_argument(
        "--elim-from-solution-steps",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Set final level elim to hidden solution step count "
            "(use --no-elim-from-solution-steps to keep generation elim, default: true)."
        ),
    )
    parser.add_argument("--seed", type=int, default=None, help="Batch seed (default: random).")
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show live reject-code progress for each level.",
    )
    return parser


def main(argv: list[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.start_level < 1:
        print("Error: --start-level must be >= 1.", file=sys.stderr)
        return 2
    if args.end_level < args.start_level:
        print("Error: --end-level must be >= --start-level.", file=sys.stderr)
        return 2
    if args.min_size < 2 or args.max_size < 2:
        print("Error: --min-size and --max-size must be >= 2.", file=sys.stderr)
        return 2
    if args.max_size < args.min_size:
        print("Error: --max-size must be >= --min-size.", file=sys.stderr)
        return 2
    if not (0.0 <= args.min_density <= 100.0 and 0.0 <= args.max_density <= 100.0):
        print("Error: density bounds must be between 0 and 100.", file=sys.stderr)
        return 2
    if args.max_density < args.min_density:
        print("Error: --max-density must be >= --min-density.", file=sys.stderr)
        return 2
    if args.min_solution_length < 1 or args.max_solution_length < 1:
        print("Error: solution-length bounds must be >= 1.", file=sys.stderr)
        return 2
    if args.max_solution_length < args.min_solution_length:
        print(
            "Error: --max-solution-length must be >= --min-solution-length.",
            file=sys.stderr,
        )
        return 2
    if args.max_solution_length > core.MAX_PROGRAM_LIMIT:
        print(
            f"Error: --max-solution-length must be <= {core.MAX_PROGRAM_LIMIT}.",
            file=sys.stderr,
        )
        return 2
    if args.generation_execution_limit < 1:
        print("Error: --generation-execution-limit must be >= 1.", file=sys.stderr)
        return 2
    if args.max_attempts < 0:
        print("Error: --max-attempts must be >= 0.", file=sys.stderr)
        return 2
    if args.max_straight_run < 0:
        print("Error: --max-straight-run must be >= 0.", file=sys.stderr)
        return 2
    if args.min_direction_types_to_exit < 1 or args.min_direction_types_to_exit > 4:
        print("Error: --min-direction-types-to-exit must be between 1 and 4.", file=sys.stderr)
        return 2

    if args.seed is None:
        args.seed = random.SystemRandom().randrange(0, 2**63)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    args.solution_dir.mkdir(parents=True, exist_ok=True)

    show_live_progress = args.verbose and sys.stdout.isatty()
    level_count = args.end_level - args.start_level + 1
    batch_rng = random.Random(args.seed)
    max_attempts_text = "inf" if args.max_attempts == 0 else str(args.max_attempts)

    series_config = {
        "start_level": args.start_level,
        "end_level": args.end_level,
        "min_size": args.min_size,
        "max_size": args.max_size,
        "min_density": args.min_density,
        "max_density": args.max_density,
        "density_cycles": args.density_cycles,
        "density_phase": args.density_phase,
        "min_solution_length": args.min_solution_length,
        "max_solution_length": args.max_solution_length,
        "solution_cycles": args.solution_cycles,
        "solution_phase": args.solution_phase,
    }

    print(
        f"Generating custom levels {args.start_level}..{args.end_level} "
        f"(seed={args.seed}, out={args.out_dir}, solutions={args.solution_dir}, "
        f"size={args.min_size}->{args.max_size}, density={args.min_density:.1f}%..{args.max_density:.1f}%, "
        f"solution_len={args.min_solution_length}..{args.max_solution_length}, "
        f"generation_elim={args.generation_execution_limit}, max_attempts={max_attempts_text}, "
        f"seal_unreachable={'on' if args.seal_unreachable else 'off'}, "
        f"elim_from_solution_steps={'on' if args.elim_from_solution_steps else 'off'})"
    )

    for idx, level_number in enumerate(range(args.start_level, args.end_level + 1), start=1):
        t = 0.0 if level_count == 1 else (idx - 1) / (level_count - 1)
        size_t = smoothstep(t)
        size = int(round(args.min_size + (args.max_size - args.min_size) * size_t))
        size = clamp_int(size, args.min_size, args.max_size)

        density_center = 0.5 * (args.min_density + args.max_density)
        density_amp = 0.5 * (args.max_density - args.min_density)
        density_percent = density_center + density_amp * math.sin(
            2.0 * math.pi * args.density_cycles * t + args.density_phase
        )
        density_percent = clamp_float(density_percent, args.min_density, args.max_density)

        solution_center = 0.5 * (args.min_solution_length + args.max_solution_length)
        solution_amp = 0.5 * (args.max_solution_length - args.min_solution_length)
        target_solution_len = int(
            round(
                solution_center
                + solution_amp * math.sin(2.0 * math.pi * args.solution_cycles * t + args.solution_phase)
            )
        )
        target_solution_len = clamp_int(
            target_solution_len,
            args.min_solution_length,
            args.max_solution_length,
        )

        options = core.GenerateOptions(
            width=size,
            height=size,
            density=density_percent / 100.0,
            solution_length=target_solution_len,
            program_limit=target_solution_len,
            execution_limit=args.generation_execution_limit,
            max_attempts=args.max_attempts,
            max_straight_run=args.max_straight_run,
            min_direction_types_to_exit=args.min_direction_types_to_exit,
        )

        if args.verbose:
            print(
                f"Level {level_number} constraints: "
                f"size={size}x{size}, density={density_percent:.1f}%, "
                f"target_sol={target_solution_len}, plim={target_solution_len}, "
                f"elim={options.execution_limit}, max_attempts={max_attempts_text}, "
                f"max_straight_run={options.max_straight_run}, "
                f"min_direction_types_to_exit={options.min_direction_types_to_exit}"
            )

        level_seed = batch_rng.randrange(0, 2**63)
        progress_width = 0
        attempt_field_width = 3 if options.max_attempts == 0 else max(1, len(str(options.max_attempts)))
        reject_counts: dict[str, int] = {}

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
                f"Level {level_number}/{args.end_level}: attempts={attempt_text}/{max_text}, status={status_text}{reject_suffix}",
                progress_width,
                show_live_progress,
            )

        try:
            generated = core.generate_level(
                level_number,
                options,
                random.Random(level_seed),
                progress_callback=progress_callback if args.verbose else None,
            )
            sealed_unreachable_cells = finalize_generated_level(generated, args.seal_unreachable)
            apply_final_execution_limit(generated, args.elim_from_solution_steps)
        except (ValueError, RuntimeError) as exc:
            clear_progress_line(progress_width, show_live_progress)
            print(f"Error generating level {level_number}: {exc}", file=sys.stderr)
            return 2

        clear_progress_line(progress_width, show_live_progress)

        payload = build_solution_payload(
            generated=generated,
            level_seed=level_seed,
            options=options,
            level_index=idx,
            level_count=level_count,
            size=size,
            density_percent=density_percent,
            target_solution_len=target_solution_len,
            seal_unreachable=args.seal_unreachable,
            sealed_unreachable_cells=sealed_unreachable_cells,
            elim_from_solution_steps=args.elim_from_solution_steps,
            series_config=series_config,
        )

        level_path = args.out_dir / f"{level_number}.level"
        solution_path = args.solution_dir / f"{level_number}.solution.json"
        level_path.write_text(generated.level_text + "\n", encoding="utf-8")
        solution_path.write_text(
            json.dumps(payload, indent=2, sort_keys=False) + "\n",
            encoding="utf-8",
        )

        print(
            f"Level {level_number}: ok "
            f"(size={size}x{size}, density={density_percent:.1f}%, "
            f"target_sol={target_solution_len}, plim={target_solution_len}, "
            f"elim={generated.level.execution_limit}, attempts={generated.attempts_used}, "
            f"sealed_unreachable={sealed_unreachable_cells}, solution_steps={generated.solution_steps}, "
            f"min_moves_to_exit={generated.min_moves_to_exit}, "
            f"min_direction_types_to_exit={generated.min_direction_types_to_exit})"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
