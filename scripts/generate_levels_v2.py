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
    return " ".join(f"{k}={reject_counts[k]}" for k in sorted(reject_counts))


def choose_size(level_index: int, level_count: int, args: argparse.Namespace) -> int:
    if args.size is not None:
        return args.size
    t = 0.0 if level_count <= 1 else level_index / (level_count - 1)
    size_t = smoothstep(t)
    raw = args.min_size + (args.max_size - args.min_size) * size_t
    return clamp_int(int(round(raw)), args.min_size, args.max_size)


def choose_target_program_length(
    level_index: int,
    level_count: int,
    args: argparse.Namespace,
    rng: random.Random,
) -> int:
    t = 0.0 if level_count <= 1 else level_index / (level_count - 1)
    center = 0.5 * (args.min_program_length + args.max_program_length)
    amplitude = 0.5 * (args.max_program_length - args.min_program_length)
    wave = math.sin(2.0 * math.pi * args.program_cycles * t + args.program_phase)
    noise = rng.uniform(-args.program_noise, args.program_noise)
    target = int(round(center + amplitude * wave + noise))
    return clamp_int(target, args.min_program_length, args.max_program_length)


def choose_turn_op(level_number: int, mode: str, rng: random.Random) -> str:
    if mode == "right":
        return "R"
    if mode == "left":
        return "L"
    if mode == "alternate":
        return "R" if (level_number % 2 == 1) else "L"
    return "R" if rng.random() < 0.5 else "L"


def choose_start(size: int, center_jitter: int, rng: random.Random) -> tuple[int, int]:
    min_coord = 1
    max_coord = size - 2
    cx = clamp_int(size // 2, min_coord, max_coord)
    cy = clamp_int(size // 2, min_coord, max_coord)
    if center_jitter <= 0:
        return cx, cy

    max_j = min(center_jitter, max(0, size // 5))
    step = 1
    offsets = list(range(-max_j, max_j + 1, step))
    ox = offsets[rng.randrange(0, len(offsets))]
    oy = offsets[rng.randrange(0, len(offsets))]
    sx = clamp_int(cx + ox, min_coord, max_coord)
    sy = clamp_int(cy + oy, min_coord, max_coord)
    return sx, sy


def generate_irregular_spiral_route(
    size: int,
    start_x: int,
    start_y: int,
    turn_op: str,
    min_route_cells: int,
    max_route_cells: int,
    rng: random.Random,
) -> list[tuple[int, int]] | None:
    # Build a non-self-crossing route that remains compatible with a fixed
    # [S,turn,F]*n + J loop (single turn direction), but with randomized growth.
    x = start_x
    y = start_y
    dir_index = core.NORTH_DIR
    turn_delta = 1 if turn_op == "R" else -1

    route: list[tuple[int, int]] = [(x, y)]
    visited: set[tuple[int, int]] = {(x, y)}
    segment_units = 1

    while len(route) < max_route_cells:
        # Two segments share a base length (spiral-like), while pair growth is
        # randomized so each level's loop spacing differs.
        for _ in range(2):
            dx, dy = core.DIR_DELTAS[dir_index]
            for _ in range(segment_units):
                nx = x + dx
                ny = y + dy
                if not core.in_bounds(nx, ny, size, size):
                    return route if len(route) >= min_route_cells else None
                if (nx, ny) in visited:
                    return route if len(route) >= min_route_cells else None
                x = nx
                y = ny
                visited.add((x, y))
                route.append((x, y))
                if len(route) >= max_route_cells:
                    return route if len(route) >= min_route_cells else None

            dir_index = core.wrap(dir_index + turn_delta, 4)

        # Base growth roughly follows 1,1,2,2,... expansion with occasional extra
        # jumps, preventing exact deterministic spirals.
        segment_units += 1 + (1 if rng.random() < 0.35 else 0)

    return route if len(route) >= min_route_cells else None


def route_turn_count(route: list[tuple[int, int]]) -> int:
    if len(route) < 3:
        return 0
    turns = 0
    prev_dx = route[1][0] - route[0][0]
    prev_dy = route[1][1] - route[0][1]
    for i in range(2, len(route)):
        dx = route[i][0] - route[i - 1][0]
        dy = route[i][1] - route[i - 1][1]
        if (dx, dy) != (prev_dx, prev_dy):
            turns += 1
        prev_dx, prev_dy = dx, dy
    return turns


def build_board(size: int, route: list[tuple[int, int]]) -> list[list[bool]]:
    board = [[True for _ in range(size)] for _ in range(size)]
    for x, y in route:
        board[y][x] = False
    return board


def allowed_block_counts(min_program_length: int, max_program_length: int) -> list[int]:
    min_blocks = math.ceil((min_program_length - 1) / 3.0)
    max_blocks = math.floor((max_program_length - 1) / 3.0)
    if max_blocks < min_blocks:
        return []
    return list(range(int(min_blocks), int(max_blocks) + 1))


def sorted_block_counts(target_length: int, block_counts: list[int], rng: random.Random) -> list[int]:
    scored: list[tuple[float, int]] = []
    for blocks in block_counts:
        program_length = 3 * blocks + 1
        dist = abs(program_length - target_length)
        odd_bonus = 0.0 if (blocks % 2 == 1) else 0.2
        jitter = rng.random() * 0.2
        scored.append((dist + odd_bonus + jitter, blocks))
    scored.sort(key=lambda item: item[0])
    return [blocks for _, blocks in scored]


def build_program(block_count: int, turn_op: str) -> list[core.Instruction]:
    program: list[core.Instruction] = []
    for _ in range(block_count):
        program.append(core.Instruction("S", 1))
        program.append(core.Instruction(turn_op, 1))
        program.append(core.Instruction("F", 1))
    program.append(core.Instruction("J", -(3 * block_count)))
    return program


def simulate_with_trace(
    level: core.Level,
    program: list[core.Instruction],
    max_steps: int,
) -> tuple[core.RunResult, set[tuple[int, int]], set[int]]:
    visited_cells: set[tuple[int, int]] = {(level.start_x, level.start_y)}
    executed_pcs: set[int] = set()

    if not program:
        return (
            core.RunResult(
                outcome="invalid",
                steps=0,
                x=level.start_x,
                y=level.start_y,
                dir=core.NORTH_DIR,
                pc=0,
                jump_exec_count=0,
                sense_exec_count=0,
            ),
            visited_cells,
            executed_pcs,
        )

    x = level.start_x
    y = level.start_y
    dir_index = core.NORTH_DIR
    pc = 0
    steps = 0
    jump_exec_count = 0
    sense_exec_count = 0
    n = len(program)

    while steps < max_steps:
        executed_pcs.add(pc)
        inst = program[pc]
        op = inst.op.upper()

        if op == "F":
            dx, dy = core.DIR_DELTAS[dir_index]
            nx = x + dx
            ny = y + dy
            steps += 1
            if not core.in_bounds(nx, ny, level.width, level.height):
                return (
                    core.RunResult(
                        outcome="escape",
                        steps=steps,
                        x=nx,
                        y=ny,
                        dir=dir_index,
                        pc=core.wrap(pc + 1, n),
                        jump_exec_count=jump_exec_count,
                        sense_exec_count=sense_exec_count,
                    ),
                    visited_cells,
                    executed_pcs,
                )
            if level.board[ny][nx]:
                return (
                    core.RunResult(
                        outcome="crash",
                        steps=steps,
                        x=nx,
                        y=ny,
                        dir=dir_index,
                        pc=pc,
                        jump_exec_count=jump_exec_count,
                        sense_exec_count=sense_exec_count,
                    ),
                    visited_cells,
                    executed_pcs,
                )
            x = nx
            y = ny
            visited_cells.add((x, y))
            pc = core.wrap(pc + 1, n)
            continue

        if op == "L":
            dir_index = core.wrap(dir_index - 1, 4)
            pc = core.wrap(pc + 1, n)
            steps += 1
            continue

        if op == "R":
            dir_index = core.wrap(dir_index + 1, 4)
            pc = core.wrap(pc + 1, n)
            steps += 1
            continue

        if op == "S":
            dx, dy = core.DIR_DELTAS[dir_index]
            nx = x + dx
            ny = y + dy
            blocked = core.in_bounds(nx, ny, level.width, level.height) and level.board[ny][nx]
            pc = core.wrap(pc + (1 if blocked else 2), n)
            steps += 1
            sense_exec_count += 1
            continue

        if op == "J":
            offset = inst.arg if isinstance(inst.arg, int) else 1
            if offset == 0:
                offset = 1
            pc = core.wrap(pc + offset, n)
            steps += 1
            jump_exec_count += 1
            continue

        return (
            core.RunResult(
                outcome="invalid",
                steps=steps,
                x=x,
                y=y,
                dir=dir_index,
                pc=pc,
                jump_exec_count=jump_exec_count,
                sense_exec_count=sense_exec_count,
            ),
            visited_cells,
            executed_pcs,
        )

    return (
        core.RunResult(
            outcome="timeout",
            steps=steps,
            x=x,
            y=y,
            dir=dir_index,
            pc=pc,
            jump_exec_count=jump_exec_count,
            sense_exec_count=sense_exec_count,
        ),
        visited_cells,
        executed_pcs,
    )


def choose_generation_execution_limit(
    route_len: int,
    program_len: int,
    configured_limit: int,
) -> int:
    auto_limit = max(2000, int(route_len * 2.4 + program_len * 8))
    if configured_limit <= 0:
        return auto_limit
    return max(configured_limit, auto_limit)


def build_solution_payload(
    level_number: int,
    level_hash: str,
    level_seed: int,
    level: core.Level,
    solution: list[core.Instruction],
    solution_steps: int,
    min_moves_to_exit: int,
    min_direction_types_to_exit: int,
    score: float,
    instruction_coverage: float,
    visited_cell_count: int,
    route_len: int,
    route_turns: int,
    generation_execution_limit: int,
    generator_attempts: int,
    series_config: dict[str, object],
) -> dict[str, object]:
    return {
        "v": 1,
        "id": str(level_number),
        "level_hash": level_hash,
        "solution_hash": level.solution_hash,
        "solution_program": core.format_program(solution),
        "solution_steps": solution_steps,
        "min_moves_to_exit": min_moves_to_exit,
        "min_direction_types_to_exit": min_direction_types_to_exit,
        "generator": {
            "mode": "v2_irregular_spiral",
            "seed": level_seed,
            "attempts_used": generator_attempts,
            "series_config": series_config,
            "width": level.width,
            "height": level.height,
            "program_limit": level.program_limit,
            "execution_limit": level.execution_limit,
            "generation_execution_limit": generation_execution_limit,
            "instruction_coverage": round(instruction_coverage, 4),
            "visited_cell_count": visited_cell_count,
            "route_length": route_len,
            "route_turns": route_turns,
            "score": round(score, 3),
        },
        "created_at": timestamp_now_utc(),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "V2 route-first level generator. Builds a randomized irregular corridor (single-turn loop compatible), then "
            "synthesizes a compact looping program (20-50 instructions by default) that "
            "traverses the board before escaping."
        )
    )
    parser.add_argument("max_level", type=int, help="Generate up to this level number.")
    parser.add_argument("--start-level", type=int, default=1, help="Starting level number (default: 1).")
    parser.add_argument("--out-dir", type=Path, default=Path("levels"), help="Public level output directory.")
    parser.add_argument(
        "--solution-dir",
        type=Path,
        default=Path("solutions"),
        help="Private solution output directory.",
    )
    parser.add_argument("--size", type=int, default=None, help="Fixed square board size (overrides min/max size).")
    parser.add_argument("--min-size", type=int, default=200, help="Minimum square size (default: 200).")
    parser.add_argument("--max-size", type=int, default=320, help="Maximum square size (default: 320).")
    parser.add_argument(
        "--min-program-length",
        type=int,
        default=20,
        help="Minimum target program length (default: 20).",
    )
    parser.add_argument(
        "--max-program-length",
        type=int,
        default=50,
        help="Maximum target program length (default: 50).",
    )
    parser.add_argument(
        "--program-cycles",
        type=float,
        default=2.0,
        help="Oscillation cycles for target program length over the range (default: 2.0).",
    )
    parser.add_argument(
        "--program-phase",
        type=float,
        default=0.0,
        help="Program-length oscillation phase in radians (default: 0.0).",
    )
    parser.add_argument(
        "--program-noise",
        type=float,
        default=1.5,
        help="Uniform random noise added to target program length (default: 1.5).",
    )
    parser.add_argument(
        "--turn-mode",
        choices=("right", "left", "alternate", "random"),
        default="alternate",
        help="Turn preference used by [S,turn,F] loop blocks (default: alternate).",
    )
    parser.add_argument(
        "--center-jitter",
        type=int,
        default=8,
        help="Max center jitter applied to start position (default: 8).",
    )
    parser.add_argument(
        "--min-instruction-coverage",
        type=float,
        default=0.85,
        help="Minimum executed-instruction coverage ratio (default: 0.85).",
    )
    parser.add_argument(
        "--min-direction-types-to-exit",
        type=int,
        default=2,
        help="Minimum distinct directions required by movement-only exit paths (1-4, default: 2).",
    )
    parser.add_argument(
        "--min-route-cells",
        type=int,
        default=500,
        help="Minimum open route cell count required (default: 500).",
    )
    parser.add_argument(
        "--max-route-cells",
        type=int,
        default=22000,
        help="Maximum route cells before forced stop (default: 22000).",
    )
    parser.add_argument(
        "--generation-execution-limit",
        type=int,
        default=0,
        help="Generation simulation cap (0 = auto based on route length, default: 0).",
    )
    parser.add_argument(
        "--best-of",
        type=int,
        default=3,
        help="Generate this many valid candidates per level and keep the best score (default: 3).",
    )
    parser.add_argument(
        "--candidate-attempts",
        type=int,
        default=300,
        help="Max candidate attempts per level (0 = infinite, default: 300).",
    )
    parser.add_argument(
        "--elim-from-solution-steps",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Set final level elim to hidden solution steps "
            "(use --no-elim-from-solution-steps to keep generation limit, default: true)."
        ),
    )
    parser.add_argument("--seed", type=int, default=None, help="Batch seed (default: random).")
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show live one-line progress with reject code counters.",
    )
    return parser


def main(argv: list[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.start_level < 1:
        print("Error: --start-level must be >= 1.", file=sys.stderr)
        return 2
    if args.max_level < args.start_level:
        print("Error: max_level must be >= --start-level.", file=sys.stderr)
        return 2
    if args.size is not None and args.size < 2:
        print("Error: --size must be >= 2.", file=sys.stderr)
        return 2
    if args.min_size < 2 or args.max_size < 2:
        print("Error: --min-size and --max-size must be >= 2.", file=sys.stderr)
        return 2
    if args.max_size < args.min_size:
        print("Error: --max-size must be >= --min-size.", file=sys.stderr)
        return 2
    if args.max_program_length < args.min_program_length:
        print("Error: --max-program-length must be >= --min-program-length.", file=sys.stderr)
        return 2
    if args.min_program_length < 4:
        print("Error: --min-program-length must be >= 4.", file=sys.stderr)
        return 2
    if args.max_program_length > core.MAX_PROGRAM_LIMIT:
        print(f"Error: --max-program-length must be <= {core.MAX_PROGRAM_LIMIT}.", file=sys.stderr)
        return 2
    if args.min_instruction_coverage <= 0 or args.min_instruction_coverage > 1.0:
        print("Error: --min-instruction-coverage must be in (0, 1].", file=sys.stderr)
        return 2
    if args.min_direction_types_to_exit < 1 or args.min_direction_types_to_exit > 4:
        print("Error: --min-direction-types-to-exit must be between 1 and 4.", file=sys.stderr)
        return 2
    if args.min_route_cells < 1:
        print("Error: --min-route-cells must be >= 1.", file=sys.stderr)
        return 2
    if args.max_route_cells < args.min_route_cells:
        print("Error: --max-route-cells must be >= --min-route-cells.", file=sys.stderr)
        return 2
    if args.candidate_attempts < 0:
        print("Error: --candidate-attempts must be >= 0.", file=sys.stderr)
        return 2
    if args.best_of < 1:
        print("Error: --best-of must be >= 1.", file=sys.stderr)
        return 2
    if args.candidate_attempts > 0 and args.best_of > args.candidate_attempts:
        print("Error: --best-of cannot exceed --candidate-attempts when attempts are finite.", file=sys.stderr)
        return 2

    block_counts = allowed_block_counts(args.min_program_length, args.max_program_length)
    if not block_counts:
        print(
            "Error: program length range does not permit any [S,turn,F]*n + J program length.",
            file=sys.stderr,
        )
        return 2

    if args.seed is None:
        args.seed = random.SystemRandom().randrange(0, 2**63)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    args.solution_dir.mkdir(parents=True, exist_ok=True)

    level_count = args.max_level - args.start_level + 1
    batch_rng = random.Random(args.seed)
    show_live_progress = args.verbose and sys.stdout.isatty()
    max_candidate_attempts_text = "inf" if args.candidate_attempts == 0 else str(args.candidate_attempts)

    print(
        f"Generating V2 levels {args.start_level}..{args.max_level} "
        f"(seed={args.seed}, out={args.out_dir}, solutions={args.solution_dir}, "
        f"size={'fixed '+str(args.size) if args.size is not None else str(args.min_size)+'->'+str(args.max_size)}, "
        f"program_len={args.min_program_length}..{args.max_program_length}, "
        f"turn_mode={args.turn_mode}, best_of={args.best_of}, candidate_attempts={max_candidate_attempts_text}, "
        f"min_instruction_coverage={args.min_instruction_coverage:.2f}, "
        f"elim_from_solution_steps={'on' if args.elim_from_solution_steps else 'off'})"
    )

    series_config = {
        "size": args.size,
        "min_size": args.min_size,
        "max_size": args.max_size,
        "min_program_length": args.min_program_length,
        "max_program_length": args.max_program_length,
        "program_cycles": args.program_cycles,
        "program_phase": args.program_phase,
        "program_noise": args.program_noise,
        "turn_mode": args.turn_mode,
        "center_jitter": args.center_jitter,
        "min_instruction_coverage": args.min_instruction_coverage,
        "min_direction_types_to_exit": args.min_direction_types_to_exit,
        "min_route_cells": args.min_route_cells,
        "max_route_cells": args.max_route_cells,
    }

    for level_offset, level_number in enumerate(range(args.start_level, args.max_level + 1)):
        size = choose_size(level_offset, level_count, args)
        target_program_length = choose_target_program_length(level_offset, level_count, args, batch_rng)

        progress_width = 0
        reject_counts: dict[str, int] = {}
        candidate_pool: list[dict[str, object]] = []
        attempts_used = 0

        if args.verbose:
            print(
                f"Level {level_number} constraints: "
                f"size={size}x{size}, target_program_len={target_program_length}, "
                f"allowed_lens={3*block_counts[0]+1}..{3*block_counts[-1]+1}, "
                f"best_of={args.best_of}, candidate_attempts={max_candidate_attempts_text}"
            )

        while len(candidate_pool) < args.best_of and (
            args.candidate_attempts == 0 or attempts_used < args.candidate_attempts
        ):
            attempts_used += 1
            level_seed = batch_rng.randrange(0, 2**63)
            rng = random.Random(level_seed)

            turn_op = choose_turn_op(level_number, args.turn_mode, rng)
            start_x, start_y = choose_start(size, args.center_jitter, rng)
            route = generate_irregular_spiral_route(
                size=size,
                start_x=start_x,
                start_y=start_y,
                turn_op=turn_op,
                min_route_cells=args.min_route_cells,
                max_route_cells=args.max_route_cells,
                rng=rng,
            )
            if route is None:
                reject_counts["rw"] = reject_counts.get("rw", 0) + 1
                if show_live_progress:
                    progress_width = update_progress_line(
                        f"Level {level_number}/{args.max_level}: attempts={attempts_used}/{max_candidate_attempts_text}, "
                        f"best_of={len(candidate_pool)}/{args.best_of}, status=rejected(rw), "
                        f"{format_reject_counts(reject_counts)}",
                        progress_width,
                        show_live_progress,
                    )
                continue
            if len(route) < args.min_route_cells:
                reject_counts["rt"] = reject_counts.get("rt", 0) + 1
                continue
            if len(set(route)) != len(route):
                reject_counts["lp"] = reject_counts.get("lp", 0) + 1
                continue

            board = build_board(size, route)
            block_candidates = sorted_block_counts(target_program_length, block_counts, rng)
            accepted_candidate = None
            last_reject_code = "cv"
            for block_count in block_candidates:
                program = build_program(block_count, turn_op)
                program_length = len(program)
                generation_execution_limit = choose_generation_execution_limit(
                    len(route),
                    program_length,
                    args.generation_execution_limit,
                )

                level = core.Level(
                    version=2,
                    level_id=str(level_number),
                    width=size,
                    height=size,
                    board=board,
                    start_x=start_x,
                    start_y=start_y,
                    start_dir=core.NORTH_DIR,
                    program_limit=program_length,
                    execution_limit=generation_execution_limit,
                    solution_hash=None,
                )

                run_result, visited_cells, executed_pcs = simulate_with_trace(
                    level,
                    program,
                    generation_execution_limit,
                )
                if run_result.outcome != "escape":
                    last_reject_code = "te"
                    continue

                instruction_coverage = len(executed_pcs) / float(program_length)
                if instruction_coverage < args.min_instruction_coverage:
                    last_reject_code = "cv"
                    continue

                if core.has_easy_two_direction_program(level):
                    last_reject_code = "pl"
                    continue

                min_moves_to_exit = core.minimum_moves_to_exit(level)
                if min_moves_to_exit is None:
                    last_reject_code = "np"
                    continue
                min_direction_types_to_exit = core.minimum_distinct_directions_to_exit(level)
                if min_direction_types_to_exit is None:
                    last_reject_code = "np"
                    continue
                if min_direction_types_to_exit < args.min_direction_types_to_exit:
                    last_reject_code = "md"
                    continue

                route_turns = route_turn_count(route)
                score = (
                    run_result.steps
                    + min_moves_to_exit * 6.0
                    + route_turns * 24.0
                    + instruction_coverage * 1200.0
                )

                solution_hash = core.compute_program_hash(program)
                level.solution_hash = solution_hash
                if args.elim_from_solution_steps:
                    level.execution_limit = max(1, run_result.steps)

                level_text = core.format_level(level)
                level_hash = core.compute_level_hash(level)

                accepted_candidate = {
                    "level_seed": level_seed,
                    "level": level,
                    "level_text": level_text,
                    "level_hash": level_hash,
                    "solution": program,
                    "solution_steps": run_result.steps,
                    "min_moves_to_exit": min_moves_to_exit,
                    "min_direction_types_to_exit": min_direction_types_to_exit,
                    "instruction_coverage": instruction_coverage,
                    "visited_cell_count": len(visited_cells),
                    "route_length": len(route),
                    "route_turns": route_turns,
                    "generation_execution_limit": generation_execution_limit,
                    "score": score,
                    "attempts_used": attempts_used,
                }
                break

            if accepted_candidate is None:
                reject_counts[last_reject_code] = reject_counts.get(last_reject_code, 0) + 1
                if show_live_progress:
                    progress_width = update_progress_line(
                        f"Level {level_number}/{args.max_level}: attempts={attempts_used}/{max_candidate_attempts_text}, "
                        f"best_of={len(candidate_pool)}/{args.best_of}, status=rejected({last_reject_code}), "
                        f"{format_reject_counts(reject_counts)}",
                        progress_width,
                        show_live_progress,
                    )
                continue

            candidate_pool.append(accepted_candidate)

            if show_live_progress:
                best_score = max(item["score"] for item in candidate_pool)
                progress_width = update_progress_line(
                    f"Level {level_number}/{args.max_level}: attempts={attempts_used}/{max_candidate_attempts_text}, "
                    f"best_of={len(candidate_pool)}/{args.best_of}, best_score={best_score:.1f}, "
                    f"status=candidate_ok, {format_reject_counts(reject_counts)}",
                    progress_width,
                    show_live_progress,
                )

        clear_progress_line(progress_width, show_live_progress)

        if len(candidate_pool) < args.best_of:
            print(
                f"Error generating level {level_number}: found {len(candidate_pool)}/{args.best_of} "
                f"valid candidates after {attempts_used} attempts "
                f"(rejects: {format_reject_counts(reject_counts)}).",
                file=sys.stderr,
            )
            return 2

        chosen = max(candidate_pool, key=lambda item: float(item["score"]))
        level = chosen["level"]
        level_text = chosen["level_text"]
        level_hash = chosen["level_hash"]
        solution = chosen["solution"]
        solution_steps = int(chosen["solution_steps"])
        min_moves_to_exit = int(chosen["min_moves_to_exit"])
        min_direction_types_to_exit = int(chosen["min_direction_types_to_exit"])
        instruction_coverage = float(chosen["instruction_coverage"])
        visited_cell_count = int(chosen["visited_cell_count"])
        route_length = int(chosen["route_length"])
        route_turns = int(chosen["route_turns"])
        generation_execution_limit = int(chosen["generation_execution_limit"])
        score = float(chosen["score"])
        level_seed = int(chosen["level_seed"])
        generator_attempts = int(chosen["attempts_used"])

        solution_payload = build_solution_payload(
            level_number=level_number,
            level_hash=level_hash,
            level_seed=level_seed,
            level=level,
            solution=solution,
            solution_steps=solution_steps,
            min_moves_to_exit=min_moves_to_exit,
            min_direction_types_to_exit=min_direction_types_to_exit,
            score=score,
            instruction_coverage=instruction_coverage,
            visited_cell_count=visited_cell_count,
            route_len=route_length,
            route_turns=route_turns,
            generation_execution_limit=generation_execution_limit,
            generator_attempts=generator_attempts,
            series_config=series_config,
        )

        level_path = args.out_dir / f"{level_number}.level"
        solution_path = args.solution_dir / f"{level_number}.solution.json"
        level_path.write_text(level_text + "\n", encoding="utf-8")
        solution_path.write_text(
            json.dumps(solution_payload, indent=2, sort_keys=False) + "\n",
            encoding="utf-8",
        )

        print(
            f"Level {level_number}: ok "
            f"(size={level.width}x{level.height}, plim={level.program_limit}, "
            f"elim={level.execution_limit}, attempts={generator_attempts}, "
            f"solution_steps={solution_steps}, route_cells={route_length}, "
            f"instruction_coverage={instruction_coverage:.3f}, score={score:.1f}, "
            f"min_moves_to_exit={min_moves_to_exit}, min_direction_types_to_exit={min_direction_types_to_exit})"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
