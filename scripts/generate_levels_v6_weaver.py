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
    "mj",  # meaningless jump
    "ct",  # trace failed
    "ms",  # min steps not met
    "js",  # missing jump/sense execution
    "sb",  # missing sense branch split
    "de",  # decoy escaped (ambiguous exit)
    "se",  # straight escape lane from start
    "ot",  # one-turn escape path from start
    "ne",  # replay did not escape
    "tc",  # immediate turn cancel
    "ux",  # unused/dead instructions
    "sr",  # straight-run limit hit
    "np",  # no movement-only path to exit
    "md",  # min direction types to exit not met
    "dn",  # density out of bounds
    "pl",  # easy short program exists
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


def timestamp_now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _in_box(x, y, start_x, start_y, radius):
    return abs(x - start_x) <= radius and abs(y - start_y) <= radius


def _build_weaver_trace(program, start_x, start_y, width, height, max_steps, entanglement_steps, box_radius, rng):
    n = len(program)
    best_result = None
    nodes_explored = 0

    def dfs(x, y, dir_index, pc, steps, reqs, state_visits, jump_exec, sense_true, sense_false, decoy_spawns):
        nonlocal best_result, nodes_explored
        nodes_explored += 1
        if nodes_explored > 30000:
            return False

        if steps >= max_steps:
            return False

        visit_key = (x, y, dir_index, pc)
        visit_count = state_visits.get(visit_key, 0)
        if visit_count > 8:  # allow high revisit for weaver to stay trapped
            return False

        new_visits = state_visits.copy()
        new_visits[visit_key] = visit_count + 1

        inst = program[pc]
        op = inst.op

        if op == "F":
            dx, dy = core.DIR_DELTAS[dir_index]
            nx, ny = x + dx, y + dy
            
            if not core.in_bounds(nx, ny, width, height):
                if steps < entanglement_steps:
                    return False
                if sense_true == 0 or sense_false == 0:
                    return False
                res = (reqs, decoy_spawns, steps + 1, jump_exec, sense_true, sense_false)
                if best_result is None or steps + 1 > best_result[2]:
                    best_result = res
                return True
                
            forced = reqs.get((nx, ny))
            if forced is True:
                return False
            new_reqs = reqs.copy()
            new_reqs[(nx, ny)] = False
            return dfs(nx, ny, dir_index, core.wrap(pc + 1, n), steps + 1, new_reqs, new_visits, jump_exec, sense_true, sense_false, decoy_spawns)

        if op == "L":
            return dfs(x, y, core.wrap(dir_index - 1, 4), core.wrap(pc + 1, n), steps + 1, reqs, new_visits, jump_exec, sense_true, sense_false, decoy_spawns)

        if op == "R":
            return dfs(x, y, core.wrap(dir_index + 1, 4), core.wrap(pc + 1, n), steps + 1, reqs, new_visits, jump_exec, sense_true, sense_false, decoy_spawns)

        if op == "J":
            offset = inst.arg if isinstance(inst.arg, int) else 1
            offset = 1 if offset == 0 else offset
            return dfs(x, y, dir_index, core.wrap(pc + offset, n), steps + 1, reqs, new_visits, jump_exec + 1, sense_true, sense_false, decoy_spawns)

        if op == "S":
            dx, dy = core.DIR_DELTAS[dir_index]
            nx, ny = x + dx, y + dy
            
            is_off_board = not core.in_bounds(nx, ny, width, height)
            if is_off_board:
                return dfs(x, y, dir_index, core.wrap(pc + 2, n), steps + 1, reqs, new_visits, jump_exec, sense_true, sense_false + 1, decoy_spawns)

            forced = reqs.get((nx, ny))
            choices = []
            if forced is None:
                choices = [True, False]
                rng.shuffle(choices)
            else:
                choices = [forced]

            success = False
            for blocked in choices:
                new_reqs = reqs.copy()
                new_reqs[(nx, ny)] = blocked
                new_decoys = list(decoy_spawns)
                
                if blocked:
                    new_decoys.append((x, y, dir_index, core.wrap(pc + 2, n)))
                    if dfs(x, y, dir_index, core.wrap(pc + 1, n), steps + 1, new_reqs, new_visits, jump_exec, sense_true + 1, sense_false, new_decoys):
                        success = True
                        if len(choices) > 1: return True
                else:
                    new_decoys.append((x, y, dir_index, core.wrap(pc + 1, n)))
                    if dfs(x, y, dir_index, core.wrap(pc + 2, n), steps + 1, new_reqs, new_visits, jump_exec, sense_true, sense_false + 1, new_decoys):
                        success = True
                        if len(choices) > 1: return True
            return success

        return False

    initial_reqs = {(start_x, start_y): False}
    sys.setrecursionlimit(max(sys.getrecursionlimit(), max_steps + 100))
    dfs(start_x, start_y, core.NORTH_DIR, 0, 0, initial_reqs, {}, 0, 0, 0, [])
    
    if best_result is not None:
        reqs, decoys, steps, jump_exec, sense_true, sense_false = best_result
        return reqs, decoys, steps, jump_exec, sense_true + sense_false, sense_true, sense_false
    return None


def _run_decoy_trace(program, start_x, start_y, start_dir, start_pc, reqs, width, height, max_steps, rng):
    x, y = start_x, start_y
    dir_index = start_dir
    pc = start_pc
    steps = 0
    n = len(program)
    state_visits = {}

    while steps < max_steps:
        visit_key = (x, y, dir_index, pc)
        visit_count = state_visits.get(visit_key, 0) + 1
        state_visits[visit_key] = visit_count
        if visit_count > 6:
            return True

        inst = program[pc]
        op = inst.op

        if op == "F":
            dx, dy = core.DIR_DELTAS[dir_index]
            nx, ny = x + dx, y + dy
            steps += 1
            if not core.in_bounds(nx, ny, width, height):
                return False

            forced = reqs.get((nx, ny))
            if forced is True:
                return True
            elif forced is None:
                if rng.random() < 0.35:
                    reqs[(nx, ny)] = True
                    return True
                else:
                    reqs[(nx, ny)] = False
            x, y = nx, ny
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
            nx, ny = x + dx, y + dy
            steps += 1
            if not core.in_bounds(nx, ny, width, height):
                pc = core.wrap(pc + 2, n)
                continue

            forced = reqs.get((nx, ny))
            if forced is None:
                blocked = rng.random() < 0.5
                reqs[(nx, ny)] = blocked
            else:
                blocked = forced
            pc = core.wrap(pc + 1, n) if blocked else core.wrap(pc + 2, n)
            continue

        if op == "J":
            offset = inst.arg if isinstance(inst.arg, int) else 1
            offset = 1 if offset == 0 else offset
            pc = core.wrap(pc + offset, n)
            steps += 1
            continue
            
        pc = core.wrap(pc + 1, n)
        steps += 1

    return True


def _weaver_random_program(length, rng):
    if length < 6:
        return core._random_program(length, rng)

    program = []
    
    f_count = max(1, length - 5)
    turn_count = length - 4 - f_count
    
    program.append(core.Instruction("S", 1))
    program.append(core.Instruction("J", 2 + f_count))
    
    program.append(core.Instruction("F", 1))
    for _ in range(f_count - 1):
        if rng.random() < 0.4:
            program.append(core.Instruction("L" if rng.random() < 0.5 else "R", 1))
        else:
            program.append(core.Instruction("F", 1))
            
    program.append(core.Instruction("J", -(2 + f_count)))
    
    for _ in range(turn_count):
        program.append(core.Instruction("L" if rng.random() < 0.5 else "R", 1))

    return program[:length]


def _try_generate_weaver_level(level_id, options, rng):
    start_x = options.width // 2
    start_y = options.height // 2

    hidden_solution = _weaver_random_program(options.solution_length, rng)
    if core.has_meaningless_jump_instruction(hidden_solution):
        return None, "mj"

    entanglement_steps = max(10, int((options.width + options.height) * options.min_steps_size_factor * 0.5))
    box_radius = max(2, min(5, options.width // 4))

    trace_result = _build_weaver_trace(
        hidden_solution, start_x, start_y, options.width, options.height, options.execution_limit, entanglement_steps, box_radius, rng
    )
    if trace_result is None:
        return None, "ct"

    reqs, decoys, steps, jump_exec, sense_exec, sense_true, sense_false = trace_result

    min_interesting_steps = max(
        10, math.floor((options.width + options.height) * options.min_steps_size_factor)
    )
    if steps < min_interesting_steps:
        return None, "ms"
    if jump_exec == 0 or sense_exec == 0:
        return None, "js"
    if sense_true == 0 or sense_false == 0:
        return None, "sb"

    for dx, dy, ddir, dpc in decoys:
        success = _run_decoy_trace(
            hidden_solution, dx, dy, ddir, dpc, reqs, options.width, options.height, 40, rng
        )
        if not success:
            return None, "de"

    board = []
    for y in range(options.height):
        row = []
        for x in range(options.width):
            if (x, y) in reqs:
                row.append(bool(reqs[(x, y)]))
            else:
                row.append(rng.random() < options.density)
        board.append(row)

    board[start_y][start_x] = False

    if core.has_straight_escape_lane_from_start(board, start_x, start_y):
        return None, "se"
    if core.has_one_turn_escape_path_from_start(board, start_x, start_y):
        return None, "ot"

    level = core.Level(
        version=2,
        level_id=None if level_id is None else str(level_id),
        width=options.width,
        height=options.height,
        board=board,
        start_x=start_x,
        start_y=start_y,
        start_dir=core.NORTH_DIR,
        program_limit=options.program_limit,
        execution_limit=options.execution_limit,
        solution_hash=None,
    )

    result = core.simulate_program(level, hidden_solution, options.execution_limit)
    if result.outcome != "escape":
        return None, "ne"

    has_turn_cancel, has_dead_inst = core.analyze_execution_path(level, hidden_solution, options.execution_limit)
    if has_turn_cancel:
        return None, "tc"
    if has_dead_inst:
        return None, "ux"
    if core.has_straight_run_at_least(level, hidden_solution, options.max_straight_run, options.execution_limit):
        return None, "sr"

    min_direction_types = core.minimum_distinct_directions_to_exit(level)
    if min_direction_types is None:
        return None, "np"
    if min_direction_types < options.min_direction_types_to_exit:
        return None, "md"

    blocked = core.block_count(board)
    ratio = blocked / (options.width * options.height)
    if ratio < 0.05 or ratio > 0.85:
        return None, "dn"

    if core.has_easy_two_direction_program(level):
        return None, "pl"

    return (level, hidden_solution, result.steps), "ok"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "V6 Weaver generator. Entangles the path through bounding box constraints "
            "so the robot visits the same central location multiple times."
        )
    )
    parser.add_argument("level_number", type=int, help="Generate exactly this level number.")
    parser.add_argument("--out-dir", type=Path, default=Path("levels"), help="Public level output directory.")
    parser.add_argument("--solution-dir", type=Path, default=Path("solutions"), help="Private solution output directory.")

    parser.add_argument("--size", type=int, default=None, help="Fixed square board size.")
    parser.add_argument("--min-size", type=int, default=11, help="Minimum square size for progression (default: 11).")
    parser.add_argument("--max-size", type=int, default=200, help="Maximum square size for progression (default: 200).")
    parser.add_argument(
        "--progressive-intensity",
        type=float,
        default=1.0,
        help="Strength of difficulty curve ramp.",
    )
    parser.add_argument(
        "--progressive-start-level",
        type=int,
        default=1,
        help="Level index corresponding to minimum size/program settings (default: 1).",
    )
    parser.add_argument(
        "--progressive-total-levels",
        type=int,
        default=None,
        help="Total levels used by progression curve (default: level_number).",
    )

    parser.add_argument("--min-program-length", type=int, default=7, help="Minimum hidden-solution length.")
    parser.add_argument("--max-program-length", type=int, default=16, help="Maximum hidden-solution length.")
    parser.add_argument("--density", type=float, default=28.0, help="Target block density in percent (default: 28.0).")
    parser.add_argument("--max-straight-run", type=int, default=10, help="Reject straight run >= N (0 disables).")
    parser.add_argument("--min-direction-types-to-exit", type=int, default=2, help="Min direction types needed to exit.")
    
    parser.add_argument("--best-of", type=int, default=1, help="Generate this many candidates and keep the best.")
    parser.add_argument("--max-attempts", type=int, default=5000, help="Max generation attempts per level.")
    
    parser.add_argument(
        "--seal-unreachable",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Seal unreachable open cells before writing level (default: true).",
    )

    parser.add_argument("--seed", type=int, default=None, help="Global RNG seed for reproducible batches.")
    parser.add_argument("--verbose", action="store_true", help="Show verbose output.")
    parser.add_argument(
        "--show-reject-codes",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Show per-reason reject codes in live progress output.",
    )

    return parser


def main(argv: list[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.level_number < 1:
        print("Error: level_number must be >= 1", file=sys.stderr)
        return 2
    if args.progressive_start_level < 1:
        print("Error: --progressive-start-level must be >= 1", file=sys.stderr)
        return 2
    if args.progressive_total_levels is None:
        args.progressive_total_levels = args.level_number
    if args.progressive_total_levels < args.progressive_start_level:
        print("Error: --progressive-total-levels must be >= --progressive-start-level", file=sys.stderr)
        return 2
    if args.progressive_total_levels < args.level_number:
        print("Error: --progressive-total-levels must be >= level_number", file=sys.stderr)
        return 2
    args.start_level = args.level_number
    args.max_level = args.level_number

    if args.seed is None:
        args.seed = random.SystemRandom().randrange(0, 2**63)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    args.solution_dir.mkdir(parents=True, exist_ok=True)

    batch_rng = random.Random(args.seed)
    show_live_progress = sys.stdout.isatty()

    print(
        f"Generating V6 levels {args.start_level}..{args.max_level} "
        f"(seed={args.seed}, out={args.out_dir}, solutions={args.solution_dir}, "
        f"Weaver mode)"
    )

    for level_number in (args.level_number,):
        span = max(1, args.progressive_total_levels - args.progressive_start_level)
        progress = (level_number - args.progressive_start_level) / span
        progress = min(1.0, max(0.0, progress))
        
        current_size = args.size if args.size is not None else args.min_size + int((args.max_size - args.min_size) * progress)
        current_size = max(args.min_size, min(args.max_size, current_size))

        target_sol_len = args.min_program_length + int((args.max_program_length - args.min_program_length) * progress)
        target_sol_len = max(args.min_program_length, min(args.max_program_length, target_sol_len))

        options = core.GenerateOptions(
            width=current_size,
            height=current_size,
            density=args.density / 100.0,
            solution_length=target_sol_len,
            program_limit=args.max_program_length,
            execution_limit=1500 + current_size * 20,
            max_attempts=args.max_attempts,
            max_straight_run=args.max_straight_run,
            min_direction_types_to_exit=args.min_direction_types_to_exit,
            min_steps_size_factor=0.6,
        )

        candidate_pool = []
        reject_counts: dict[str, int] = {}
        evals = 0

        while len(candidate_pool) < args.best_of and evals < options.max_attempts:
            evals += 1
            candidate_seed = batch_rng.randrange(0, 2**63)
            rng = random.Random(candidate_seed)

            generated, reject_code = _try_generate_weaver_level(level_number, options, rng)
            
            if generated is None:
                reject_counts[reject_code] = reject_counts.get(reject_code, 0) + 1
            else:
                base_level, solution, solution_steps = generated
                candidate_pool.append((base_level, solution, solution_steps, candidate_seed))
            
            if show_live_progress:
                status = "searching" if generated is None else "found_candidate"
                counts = format_reject_counts(reject_counts) if args.show_reject_codes else ""
                sys.stdout.write(f"\rLevel {level_number}: evals={evals}/{options.max_attempts} best_of={len(candidate_pool)}/{args.best_of} status={status} rejects: {counts}       ")
                sys.stdout.flush()

        if show_live_progress:
            sys.stdout.write("\r" + " " * 120 + "\r")
            sys.stdout.flush()

        if not candidate_pool:
            print(f"Error generating level {level_number}: failed to find candidate in {options.max_attempts} attempts. Rejects: {format_reject_counts(reject_counts)}", file=sys.stderr)
            return 2

        best_candidate = max(candidate_pool, key=lambda c: c[2])
        base_level, solution, solution_steps, candidate_seed = best_candidate

        solution_hash = core.compute_program_hash(solution)
        level = core.Level(
            version=base_level.version,
            level_id=base_level.level_id,
            width=base_level.width,
            height=base_level.height,
            board=base_level.board,
            start_x=base_level.start_x,
            start_y=base_level.start_y,
            start_dir=base_level.start_dir,
            program_limit=base_level.program_limit,
            execution_limit=base_level.execution_limit,
            solution_hash=solution_hash,
        )

        sealed_unreachable_cells = 0
        if args.seal_unreachable:
            sealed_unreachable_cells = core.seal_unreachable_cells(level)

        level_text = core.format_level(level)
        level_hash = core.compute_level_hash(level)
        solution_text = core.format_program(solution)
        
        min_moves_to_exit = core.minimum_moves_to_exit(level)
        min_direction_types_to_exit = core.minimum_distinct_directions_to_exit(level)

        level_path = args.out_dir / f"{level_number}.level"
        solution_path = args.solution_dir / f"{level_number}.solution.json"
        
        payload = {
            "v": 1,
            "id": level.level_id,
            "level_hash": level_hash,
            "solution_hash": level.solution_hash,
            "solution_program": solution_text,
            "solution_steps": solution_steps,
            "min_moves_to_exit": min_moves_to_exit,
            "min_direction_types_to_exit": min_direction_types_to_exit,
            "generator": {
                "mode": "v6_weaver",
                "seed": candidate_seed,
                "width": options.width,
                "height": options.height,
                "target_solution_len": options.solution_length,
                "sealed_unreachable_cells": sealed_unreachable_cells,
                "attempts_used": evals,
            },
            "created_at": timestamp_now_utc(),
        }

        level_path.write_text(level_text + "\n", encoding="utf-8")
        solution_path.write_text(
            json.dumps(payload, indent=2, sort_keys=False) + "\n",
            encoding="utf-8",
        )

        print(f"Level {level_number}: ok (size={current_size}x{current_size}, "
              f"target_sol={target_sol_len}, sealed={sealed_unreachable_cells}, "
              f"steps={solution_steps}, evals={evals})")

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
