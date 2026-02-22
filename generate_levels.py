#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

import sensejump_core as core


def timestamp_now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def clamp_int(value: int, min_value: int, max_value: int) -> int:
    return max(min_value, min(max_value, value))


def clamp_float(value: float, min_value: float, max_value: float) -> float:
    return max(min_value, min(max_value, value))


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


REJECT_CODE_ORDER = (
    "sr",  # straight-run limit
    "md",  # min direction types
    "pl",  # easy path within program limit
    "ux",  # unused instructions
    "mj",  # meaningless jump
    "ct",  # constraint trace failed
    "ms",  # min-step threshold not met (trace)
    "js",  # missing jump/sense execution in trace
    "sb",  # sense branch split missing in trace
    "se",  # straight escape lane from start
    "ot",  # one-turn escape path from start
    "ne",  # hidden solution did not escape
    "rj",  # missing jump/sense execution in replay
    "rs",  # min-step threshold not met (replay)
    "tc",  # immediate turn-cancel pattern
    "np",  # no movement-only path to edge subset
    "dn",  # density outside accepted range
)


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


def choose_level_options(
    level_number: int,
    start_level: int,
    max_level: int,
    base_size: int,
    args: argparse.Namespace,
    level_rng: random.Random,
) -> core.GenerateOptions:
    span = max(1, max_level - start_level)
    progress = (level_number - start_level) / span
    intensity = float(args.progressive_intensity)

    if not args.progressive_difficulty:
        return core.GenerateOptions(
            width=base_size,
            height=base_size,
            density=args.density / 100.0,
            solution_length=args.solution_length,
            program_limit=args.program_limit,
            execution_limit=args.execution_limit,
            max_attempts=args.max_attempts,
            max_straight_run=args.max_straight_run,
            min_direction_types_to_exit=args.min_direction_types_to_exit,
        )

    # Keep progression smooth at high intensity; avoid saturating by level 2.
    speed_scale = 1.0 + 0.18 * (math.sqrt(intensity) - 1.0)
    effective_progress = clamp_float(progress * speed_scale, 0.0, 1.0)
    wave_scale = clamp_float(1.0 + 0.25 * (intensity - 1.0), 0.6, 3.0)

    # Size increases more slowly than program complexity and fluctuates across the run.
    base_solution_reference = min(args.solution_length, max(3, base_size + 1))
    base_size_delta = max(
        2,
        int(round(base_solution_reference * 0.45)),
        int(round(span * 0.05)),
    )
    scaled_size_delta = max(2, int(round(base_size_delta * (0.6 + 0.4 * intensity))))
    size_target_max = min(args.progressive_max_size, base_size + scaled_size_delta)
    size_trend = base_size + (size_target_max - base_size) * effective_progress
    size_wave = wave_scale * math.sin(level_number * 0.37 + 1.2) + level_rng.uniform(-0.6, 0.6) * wave_scale
    size = int(round(size_trend + size_wave))
    size_floor = base_size + int((size_target_max - base_size) * effective_progress * 0.5)
    size = max(size_floor, size)
    size = clamp_int(size, max(4, base_size), size_target_max)

    # Base target length scales with board size, then we add progressive variation.
    solution_per_size = base_solution_reference / max(1, base_size)
    baseline_solution_length = clamp_int(
        int(round(size * solution_per_size)),
        3,
        core.MAX_PROGRAM_LIMIT - 1,
    )

    # Mostly scale difficulty via solution/program length.
    base_solution_delta = max(4, int(round(span * 0.14)))
    intensity_scale = 1.0 + 0.45 * (math.sqrt(intensity) - 1.0)
    size_bonus = max(
        0,
        int(round((size - base_size) * (0.15 + 0.03 * (intensity - 1.0)))),
    )
    scaled_solution_delta = max(1, int(round(base_solution_delta * intensity_scale + size_bonus)))
    size_delta_cap = max(6, int(round(size * (0.22 + 0.02 * math.sqrt(intensity)))))
    scaled_solution_delta = min(scaled_solution_delta, size_delta_cap)
    solution_target_max = min(core.MAX_PROGRAM_LIMIT - 1, baseline_solution_length + scaled_solution_delta)
    solution_trend = baseline_solution_length + (solution_target_max - baseline_solution_length) * effective_progress
    solution_wave_raw = 1.1 * math.sin(level_number * 0.73) + 0.9 * math.sin(level_number * 0.21 + 0.4)
    solution_wave_anchor = 1.1 * math.sin(start_level * 0.73) + 0.9 * math.sin(start_level * 0.21 + 0.4)
    solution_wave = wave_scale * (solution_wave_raw - solution_wave_anchor)
    solution_noise = level_rng.uniform(-0.7, 0.7) * wave_scale
    solution_length = int(round(solution_trend + solution_wave + solution_noise))
    solution_floor = baseline_solution_length + int(
        (solution_target_max - baseline_solution_length)
        * effective_progress
        * clamp_float(0.55 + 0.05 * intensity, 0.45, 0.9)
    )
    solution_length = max(solution_floor, solution_length)
    solution_length = clamp_int(solution_length, 3, core.MAX_PROGRAM_LIMIT - 1)

    # Vary slack so some levels are tight and others have exploration room.
    slack_center = 4.0 - 1.5 * effective_progress - 0.35 * (intensity - 1.0)
    slack = int(round(slack_center + level_rng.uniform(-1.0, 1.0)))
    slack = clamp_int(slack, 2, 6)
    program_limit = clamp_int(solution_length + slack, max(4, solution_length), core.MAX_PROGRAM_LIMIT)

    # Fluctuate fill level while trending a bit denser over time.
    density_base = float(args.density)
    density_ceiling = min(
        68.0,
        max(density_base + 6.0 * math.sqrt(intensity), density_base + span * 0.08 * intensity),
    )
    density_floor = max(8.0, density_base - (8.0 + 2.0 * (intensity - 1.0)))
    density_trend = density_base + (density_ceiling - density_base) * (0.35 + 0.5 * effective_progress)
    density_wave = (4.5 + 1.1 * (wave_scale - 1.0)) * math.sin(level_number * 0.59 + 2.3) + (
        2.0 + 0.6 * (wave_scale - 1.0)
    ) * math.sin(level_number * 0.17 + 0.8)
    density_noise = level_rng.uniform(-1.8, 1.8) * (1.0 + 0.3 * (wave_scale - 1.0))
    density_percent = clamp_float(density_trend + density_wave + density_noise, density_floor, density_ceiling)

    # Give larger/longer levels a higher execution budget.
    execution_scale = 1.0 + 0.35 * (intensity - 1.0)
    execution_bonus = int(
        round(
            (
                (solution_length - baseline_solution_length) * 28
                + (size - base_size) * int(18 + 8 * effective_progress)
            )
            * execution_scale
        )
    )
    execution_wave = int(
        round(
            (level_rng.uniform(-25, 45) + 20 * math.sin(level_number * 0.29 + 0.5))
            * (1.0 + 0.25 * (wave_scale - 1.0))
        )
    )
    execution_limit = clamp_int(args.execution_limit + execution_bonus + execution_wave, args.execution_limit, 15000)

    attempt_scale = 1.0 + 0.6 * (intensity - 1.0)
    max_attempts = args.max_attempts + int(round(150 * effective_progress * attempt_scale))

    return core.GenerateOptions(
        width=size,
        height=size,
        density=density_percent / 100.0,
        solution_length=solution_length,
        program_limit=program_limit,
        execution_limit=execution_limit,
        max_attempts=max_attempts,
        max_straight_run=args.max_straight_run,
        min_direction_types_to_exit=args.min_direction_types_to_exit,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Generate multiple SenseJump levels, with public .level files and private .solution.json files."
        )
    )
    parser.add_argument("max_level", type=int, help="Generate up to this level number.")
    parser.add_argument(
        "--start-level",
        type=int,
        default=1,
        help="Starting level number (default: 1).",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("levels"),
        help="Public level output directory (default: levels).",
    )
    parser.add_argument(
        "--solution-dir",
        type=Path,
        default=Path("solutions"),
        help="Private solution output directory (default: solutions).",
    )
    parser.add_argument("--width", type=int, default=11, help="Legacy base size input (default: 11).")
    parser.add_argument("--height", type=int, default=11, help="Legacy input, ignored (boards are square).")
    parser.add_argument(
        "--size",
        type=int,
        default=None,
        help="Base square board size (overrides --width/--height).",
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
        help="Program length limit stored in levels (default: 14).",
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
        help="Max generation attempts per level (default: 650).",
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
        "--seed",
        type=int,
        default=None,
        help="Global RNG seed for reproducible batches (default: random).",
    )
    parser.add_argument(
        "--progressive-difficulty",
        action="store_true",
        help=(
            "Increase difficulty with level number. Primarily scales solution/program length, "
            "and also varies square board size and density."
        ),
    )
    parser.add_argument(
        "--level-seed-retries",
        type=int,
        default=0,
        help=(
            "How many different per-level RNG seeds to try before failing a level "
            "(0 = infinite, default: 0)."
        ),
    )
    parser.add_argument(
        "--best-of",
        type=int,
        default=1,
        help=(
            "Generate this many valid candidates per level and keep the one with the "
            "highest min_moves_to_exit (default: 1)."
        ),
    )
    parser.add_argument(
        "--show-reject-codes",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Show per-reason reject codes in live progress output "
            "(use --no-show-reject-codes to hide, default: true)."
        ),
    )
    parser.add_argument(
        "--seal-unreachable",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "After generation, mark open cells unreachable from the start as blocked "
            "before writing files (use --no-seal-unreachable to disable, default: true)."
        ),
    )
    parser.add_argument(
        "--progressive-intensity",
        type=float,
        default=1.0,
        help=(
            "Strength of progressive ramp when --progressive-difficulty is on. "
            "1.0 = current baseline, higher = harder/faster ramp (default: 1.0)."
        ),
    )
    parser.add_argument(
        "--progressive-max-size",
        type=int,
        default=128,
        help="Maximum board size used by progressive mode (default: 128).",
    )
    return parser


def build_solution_payload(
    generated: core.GeneratedLevel,
    level_seed: int,
    best_of: int,
    seal_unreachable: bool,
    sealed_unreachable_cells: int,
    progressive_difficulty: bool,
    progressive_intensity: float,
    progressive_max_size: int,
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
            "best_of": best_of,
            "seal_unreachable": seal_unreachable,
            "sealed_unreachable_cells": sealed_unreachable_cells,
            "attempts_used": generated.attempts_used,
            "progressive_difficulty": progressive_difficulty,
            "progressive_intensity": progressive_intensity,
            "progressive_max_size": progressive_max_size,
            "width": options.width,
            "height": options.height,
            "density_percent": round(options.density * 100.0, 2),
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

    if args.start_level < 1:
        print("Error: --start-level must be >= 1", file=sys.stderr)
        return 2
    if args.max_level < args.start_level:
        print("Error: max_level must be >= --start-level", file=sys.stderr)
        return 2

    if args.seed is None:
        args.seed = random.SystemRandom().randrange(0, 2**63)

    base_size = args.size if args.size is not None else args.width
    if base_size < 2:
        print("Error: base square size must be >= 2.", file=sys.stderr)
        return 2
    if args.progressive_intensity <= 0:
        print("Error: --progressive-intensity must be > 0.", file=sys.stderr)
        return 2
    if args.progressive_max_size < 2:
        print("Error: --progressive-max-size must be >= 2.", file=sys.stderr)
        return 2
    if args.max_straight_run < 0:
        print("Error: --max-straight-run must be >= 0.", file=sys.stderr)
        return 2
    if args.min_direction_types_to_exit < 1 or args.min_direction_types_to_exit > 4:
        print("Error: --min-direction-types-to-exit must be between 1 and 4.", file=sys.stderr)
        return 2
    if args.level_seed_retries < 0:
        print("Error: --level-seed-retries must be >= 0.", file=sys.stderr)
        return 2
    if args.best_of < 1:
        print("Error: --best-of must be >= 1.", file=sys.stderr)
        return 2
    if args.level_seed_retries > 0 and args.best_of > args.level_seed_retries:
        print("Error: --best-of cannot exceed --level-seed-retries when retries are finite.", file=sys.stderr)
        return 2
    if args.progressive_difficulty and args.progressive_max_size < base_size:
        print(
            "Error: --progressive-max-size cannot be smaller than the base --size/--width "
            "when --progressive-difficulty is enabled.",
            file=sys.stderr,
        )
        return 2
    if args.height != args.width and args.size is None:
        print(
            f"Info: forcing square boards; using width={args.width} and ignoring height={args.height}."
        )
    if not args.progressive_difficulty and args.solution_length > args.program_limit:
        print("Error: --solution-length cannot exceed --program-limit.", file=sys.stderr)
        return 2

    args.out_dir.mkdir(parents=True, exist_ok=True)
    args.solution_dir.mkdir(parents=True, exist_ok=True)

    batch_rng = random.Random(args.seed)
    show_live_progress = sys.stdout.isatty()
    print(
        f"Generating levels {args.start_level}..{args.max_level} "
        f"(seed={args.seed}, out={args.out_dir}, solutions={args.solution_dir}, "
        f"progressive={'on' if args.progressive_difficulty else 'off'}, "
        f"intensity={args.progressive_intensity}, progressive_max_size={args.progressive_max_size}, "
        f"max_straight_run={args.max_straight_run}, min_direction_types_to_exit={args.min_direction_types_to_exit}, "
        f"best_of={args.best_of}, reject_codes={'on' if args.show_reject_codes else 'off'}, "
        f"seal_unreachable={'on' if args.seal_unreachable else 'off'}, "
        f"square_size_base={base_size})"
    )

    for level_number in range(args.start_level, args.max_level + 1):
        candidate_pool: list[tuple[core.GeneratedLevel, core.GenerateOptions, int]] = []
        last_error = None
        reject_counts: dict[str, int] = {}
        seed_tries_used = 0
        progress_width = 0
        attempt_field_width = 1
        constraints_announced = False
        max_seed_tries_text = "inf" if args.level_seed_retries == 0 else str(args.level_seed_retries)

        def progress_status(
            status: str,
            attempt: int | None = None,
            max_attempts: int | None = None,
        ) -> None:
            nonlocal progress_width, attempt_field_width
            status_text = status
            if status.startswith("rejected:"):
                reject_code = status.split(":", 1)[1] or "??"
                reject_counts[reject_code] = reject_counts.get(reject_code, 0) + 1
                status_text = f"rejected({reject_code})" if args.show_reject_codes else "rejected"
            reject_counts_suffix = f", {format_reject_counts(reject_counts)}" if args.show_reject_codes else ""
            if not show_live_progress:
                return
            if max_attempts is not None:
                attempt_field_width = max(attempt_field_width, len(str(max_attempts)))
            elif attempt is not None:
                attempt_field_width = max(attempt_field_width, len(str(attempt)))
            attempt_text = "-" if attempt is None else str(attempt).rjust(attempt_field_width)
            max_attempts_text = "-" if max_attempts is None else str(max_attempts).rjust(attempt_field_width)
            best_min_moves = max((entry[0].min_moves_to_exit for entry in candidate_pool), default=None)
            best_min_moves_text = "-" if best_min_moves is None else str(best_min_moves)
            progress_width = update_progress_line(
                (
                    f"Level {level_number}/{args.max_level}: "
                    f"seed_tries={seed_tries_used}/{max_seed_tries_text}, "
                    f"best_of={len(candidate_pool)}/{args.best_of}, "
                    f"best_min_moves={best_min_moves_text}, "
                    f"attempts={attempt_text}/{max_attempts_text}, "
                    f"status={status_text}{reject_counts_suffix}"
                ),
                progress_width,
                show_live_progress,
            )

        while len(candidate_pool) < args.best_of and (
            args.level_seed_retries == 0 or seed_tries_used < args.level_seed_retries
        ):
            seed_tries_used += 1
            candidate_seed = batch_rng.randrange(0, 2**63)
            level_tuning_rng = random.Random(candidate_seed ^ 0x9E3779B97F4A7C15)
            candidate_options = choose_level_options(
                level_number=level_number,
                start_level=args.start_level,
                max_level=args.max_level,
                base_size=base_size,
                args=args,
                level_rng=level_tuning_rng,
            )
            if show_live_progress and not constraints_announced:
                print(
                    f"Level {level_number} constraints: "
                    f"size={candidate_options.width}x{candidate_options.height}, "
                    f"density={candidate_options.density * 100.0:.1f}%, "
                    f"target_sol={candidate_options.solution_length}, "
                    f"plim={candidate_options.program_limit}, "
                    f"elim={candidate_options.execution_limit}, "
                    f"max_attempts={candidate_options.max_attempts}, "
                    f"max_straight_run={candidate_options.max_straight_run}, "
                    f"min_direction_types_to_exit={candidate_options.min_direction_types_to_exit}, "
                    f"best_of={args.best_of}"
                )
                constraints_announced = True
            progress_status("searching", 0, candidate_options.max_attempts)
            try:
                candidate_generated = core.generate_level(
                    level_number,
                    candidate_options,
                    rng=random.Random(candidate_seed),
                    progress_callback=lambda attempt, max_attempts, status: progress_status(
                        status, attempt, max_attempts
                    ),
                )
            except (ValueError, RuntimeError) as exc:
                last_error = exc
                progress_status("seed_failed", candidate_options.max_attempts, candidate_options.max_attempts)
                continue
            candidate_pool.append((candidate_generated, candidate_options, candidate_seed))
            progress_status("candidate_ok", candidate_generated.attempts_used, candidate_options.max_attempts)

        if len(candidate_pool) < args.best_of:
            clear_progress_line(progress_width, show_live_progress)
            detail = f"{last_error}" if last_error is not None else "unknown error"
            print(
                f"Error generating level {level_number}: found {len(candidate_pool)}/{args.best_of} valid "
                f"candidates after {seed_tries_used} seed tries: {detail}",
                file=sys.stderr,
            )
            return 2

        generated, options, level_seed = max(
            candidate_pool,
            key=lambda entry: entry[0].min_moves_to_exit,
        )
        sealed_unreachable_cells = finalize_generated_level(generated, args.seal_unreachable)

        clear_progress_line(progress_width, show_live_progress)
        level_path = args.out_dir / f"{level_number}.level"
        solution_path = args.solution_dir / f"{level_number}.solution.json"
        payload = build_solution_payload(
            generated=generated,
            level_seed=level_seed,
            best_of=args.best_of,
            seal_unreachable=args.seal_unreachable,
            sealed_unreachable_cells=sealed_unreachable_cells,
            progressive_difficulty=args.progressive_difficulty,
            progressive_intensity=args.progressive_intensity,
            progressive_max_size=args.progressive_max_size,
            options=options,
        )

        level_path.write_text(generated.level_text + "\n", encoding="utf-8")
        solution_path.write_text(
            json.dumps(payload, indent=2, sort_keys=False) + "\n",
            encoding="utf-8",
        )
        print(
            f"Level {level_number}: ok "
            f"(size={options.width}x{options.height}, density={options.density * 100.0:.1f}%, "
            f"target_sol={options.solution_length}, plim={options.program_limit}, "
            f"elim={options.execution_limit}, seed_tries={seed_tries_used}, best_of={len(candidate_pool)}/{args.best_of}, "
            f"attempts={generated.attempts_used}, sealed_unreachable={sealed_unreachable_cells}, "
            f"solution_steps={generated.solution_steps}, min_moves_to_exit={generated.min_moves_to_exit}, "
            f"min_direction_types_to_exit={generated.min_direction_types_to_exit})"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
