#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import random
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import sensejump_core as core

REJECT_CODE_ORDER = (
    "pb",  # program blueprint build failed
    "mj",  # meaningless jump
    "ct",  # guided trace failed
    "ms",  # trace too short
    "js",  # no jump/sense activity
    "sb",  # no S true/false split
    "ux",  # low coverage or dead instructions
    "dv",  # low direction diversity in hidden solution
    "sp",  # low route spread
    "vc",  # low visited-cell count
    "ne",  # replay did not escape
    "sr",  # straight-run limit hit
    "tc",  # immediate turn-cancel pattern
    "pl",  # easy short two-direction program exists
    "np",  # no movement-only path to exit
    "md",  # min direction types to exit not met
    "dn",  # density target not feasible
    "cx",  # failed known counterexample program
    "ad",  # adversarial solver found short solution
)


@dataclass
class AttackOutcome:
    program: list[core.Instruction] | None
    steps: int | None
    evaluations: int
    max_steps_used: int


@dataclass
class Candidate:
    level_seed: int
    level: core.Level
    level_text: str
    level_hash: str
    solution: list[core.Instruction]
    solution_steps: int
    min_moves_to_exit: int
    min_direction_types_to_exit: int
    instruction_coverage: float
    visited_cell_count: int
    spread_ratio: float
    direction_types_used: int
    sense_true: int
    sense_false: int
    jump_exec_count: int
    generation_execution_limit: int
    sealed_unreachable_cells: int
    texture_cleanup_attempts: int
    texture_flips_applied: int
    checkerboard_2x2: int
    density_driver_percent: float
    target_density_percent: float
    final_density_percent: float
    blueprint_states: int
    blueprint_next_states: list[int]
    score: float
    attempts_used: int
    counterexample_pool_size: int
    adversary_evaluations: int
    adversary_max_steps_used: int


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


def load_v3_module():
    v3_path = ROOT_DIR / "scripts" / "generate_levels_v3.py"
    if not v3_path.exists():
        raise FileNotFoundError(f"Required file not found: {v3_path}")
    spec = importlib.util.spec_from_file_location("_robotsrevenge_v3", str(v3_path))
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load generate_levels_v3.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def effective_jump_mod(offset: int, n: int) -> int:
    if n <= 0:
        return 0
    return offset % n


def random_jump_offset(length: int, jump_span: int, rng: random.Random) -> int:
    if length <= 1:
        return 2
    span = max(2, min(jump_span, max(2, length - 1)))
    while True:
        offset = rng.randint(-span, span)
        if offset == 0:
            continue
        if effective_jump_mod(offset, length) in (0, 1):
            continue
        return offset


def weighted_random_attack_op(rng: random.Random) -> str:
    p = rng.random()
    if p < 0.42:
        return "F"
    if p < 0.58:
        return "S"
    if p < 0.70:
        return "L"
    if p < 0.82:
        return "R"
    return "J"


def clone_program(program: list[core.Instruction]) -> list[core.Instruction]:
    return [core.Instruction(inst.op, inst.arg) for inst in program]


def normalize_attack_program(
    program: list[core.Instruction],
    jump_span: int,
    rng: random.Random,
) -> list[core.Instruction]:
    if not program:
        return [core.Instruction("F", 1)]

    normalized = clone_program(program)
    length = len(normalized)
    has_f = any(inst.op == "F" for inst in normalized)
    if not has_f:
        normalized[rng.randrange(0, length)] = core.Instruction("F", 1)

    for index, inst in enumerate(normalized):
        op = inst.op.upper()
        if op != "J":
            normalized[index] = core.Instruction(op, 1)
            continue
        offset = inst.arg if isinstance(inst.arg, int) else 1
        if effective_jump_mod(offset, length) in (0, 1):
            offset = random_jump_offset(length, jump_span, rng)
        normalized[index] = core.Instruction("J", offset)

    if core.has_meaningless_jump_instruction(normalized):
        for index, inst in enumerate(normalized):
            if inst.op == "J":
                normalized[index] = core.Instruction("J", random_jump_offset(length, jump_span, rng))
    return normalized


def random_attack_program(length: int, jump_span: int, rng: random.Random) -> list[core.Instruction]:
    program: list[core.Instruction] = []
    for _ in range(length):
        op = weighted_random_attack_op(rng)
        if op == "J":
            arg = random_jump_offset(length, jump_span, rng)
            program.append(core.Instruction("J", arg))
        else:
            program.append(core.Instruction(op, 1))
    return normalize_attack_program(program, jump_span, rng)


def mutate_attack_program(
    parent: list[core.Instruction],
    jump_span: int,
    rng: random.Random,
) -> list[core.Instruction]:
    child = clone_program(parent)
    length = len(child)
    if length <= 0:
        return [core.Instruction("F", 1)]

    op = rng.random()
    if op < 0.50:
        index = rng.randrange(0, length)
        new_op = weighted_random_attack_op(rng)
        if new_op == "J":
            child[index] = core.Instruction("J", random_jump_offset(length, jump_span, rng))
        else:
            child[index] = core.Instruction(new_op, 1)
    elif op < 0.75:
        index = rng.randrange(0, length)
        inst = child[index]
        if inst.op == "J":
            child[index] = core.Instruction("J", random_jump_offset(length, jump_span, rng))
        else:
            index2 = rng.randrange(0, length)
            child[index], child[index2] = child[index2], child[index]
    else:
        if length > 2:
            a = rng.randrange(0, length - 1)
            b = rng.randrange(a + 1, length)
            segment = child[a:b]
            segment.reverse()
            child[a:b] = segment
    return normalize_attack_program(child, jump_span, rng)


def crossover_attack_program(
    parent_a: list[core.Instruction],
    parent_b: list[core.Instruction],
    jump_span: int,
    rng: random.Random,
) -> list[core.Instruction]:
    if len(parent_a) != len(parent_b) or len(parent_a) <= 1:
        return normalize_attack_program(clone_program(parent_a), jump_span, rng)
    n = len(parent_a)
    cut = rng.randrange(1, n)
    child = clone_program(parent_a[:cut]) + clone_program(parent_b[cut:])
    return normalize_attack_program(child, jump_span, rng)


def distance_to_edge(width: int, height: int, x: int, y: int) -> int:
    cx = clamp_int(x, 0, max(0, width - 1))
    cy = clamp_int(y, 0, max(0, height - 1))
    return min(cx, width - 1 - cx, cy, height - 1 - cy)


def attack_score(
    level: core.Level,
    run: core.RunResult,
    program_len: int,
    max_steps: int,
) -> float:
    if run.outcome == "escape":
        return 1_000_000_000.0 - program_len * 1_000_000.0 - run.steps * 100.0

    dist = distance_to_edge(level.width, level.height, run.x, run.y)
    score = -dist * 120.0 - program_len * 20.0 + min(max_steps, run.steps) * 0.025
    score += (run.sense_exec_count + run.jump_exec_count) * 0.16

    if run.outcome == "timeout":
        score += 10.0
    elif run.outcome == "crash":
        score -= 8.0
    elif run.outcome == "invalid":
        score -= 22.0
    return score


def choose_adversary_execution_limit(level: core.Level, args: argparse.Namespace) -> int:
    if args.adversary_execution_limit > 0:
        return args.adversary_execution_limit
    auto_limit = max(180, int(round(level.width * level.height * args.adversary_execution_scale)))
    return max(1, min(level.execution_limit, auto_limit))


def run_known_counterexamples(
    level: core.Level,
    counterexamples: list[list[core.Instruction]],
    max_steps: int,
) -> bool:
    for program in counterexamples:
        if len(program) > level.program_limit:
            continue
        result = core.simulate_program(level, program, max_steps)
        if result.outcome == "escape":
            return True
    return False


def search_adversarial_program(
    level: core.Level,
    args: argparse.Namespace,
    rng: random.Random,
    blocked_hashes: set[str],
) -> AttackOutcome:
    min_len = max(2, args.adversary_min_program_length)
    max_len = min(level.program_limit, args.adversary_max_program_length)
    if max_len < min_len or args.adversary_total_evals <= 0:
        max_steps = choose_adversary_execution_limit(level, args)
        return AttackOutcome(program=None, steps=None, evaluations=0, max_steps_used=max_steps)

    lengths = list(range(min_len, max_len + 1))
    total_budget = args.adversary_total_evals
    total_evals = 0
    max_steps = choose_adversary_execution_limit(level, args)
    jump_span = max(2, args.adversary_jump_span)

    for index, length in enumerate(lengths):
        remaining_lengths = len(lengths) - index
        remaining_budget = max(0, total_budget - total_evals)
        if remaining_budget <= 0:
            break

        per_length_budget = max(
            args.adversary_min_evals_per_length,
            remaining_budget // max(1, remaining_lengths),
        )
        per_length_budget = min(per_length_budget, remaining_budget)
        if per_length_budget <= 0:
            continue

        pop_size = clamp_int(args.adversary_population, 4, max(4, per_length_budget))
        population = [random_attack_program(length, jump_span, rng) for _ in range(pop_size)]

        evals_for_length = 0
        while evals_for_length < per_length_budget:
            scored: list[tuple[float, list[core.Instruction]]] = []

            for program in population:
                if evals_for_length >= per_length_budget:
                    break
                program_hash = core.compute_program_hash(program)
                if program_hash in blocked_hashes:
                    evals_for_length += 1
                    total_evals += 1
                    continue

                result = core.simulate_program(level, program, max_steps)
                evals_for_length += 1
                total_evals += 1

                if result.outcome == "escape":
                    return AttackOutcome(
                        program=program,
                        steps=result.steps,
                        evaluations=total_evals,
                        max_steps_used=max_steps,
                    )

                score = attack_score(level, result, len(program), max_steps)
                scored.append((score, program))

            if not scored:
                population = [random_attack_program(length, jump_span, rng) for _ in range(pop_size)]
                continue

            scored.sort(key=lambda item: item[0], reverse=True)
            elites = [clone_program(item[1]) for item in scored[: max(2, pop_size // 4)]]
            next_population: list[list[core.Instruction]] = [clone_program(p) for p in elites]

            while len(next_population) < pop_size:
                if rng.random() < args.adversary_crossover_rate and len(elites) >= 2:
                    pa = elites[rng.randrange(0, len(elites))]
                    pb = elites[rng.randrange(0, len(elites))]
                    child = crossover_attack_program(pa, pb, jump_span, rng)
                else:
                    pa = elites[rng.randrange(0, len(elites))]
                    child = clone_program(pa)

                if rng.random() < args.adversary_mutation_rate:
                    child = mutate_attack_program(child, jump_span, rng)
                else:
                    child = normalize_attack_program(child, jump_span, rng)
                next_population.append(child)

            population = next_population

    return AttackOutcome(
        program=None,
        steps=None,
        evaluations=total_evals,
        max_steps_used=max_steps,
    )


def build_solution_payload(
    level_number: int,
    candidate: Candidate,
    series_config: dict[str, object],
    adversary_config: dict[str, object],
) -> dict[str, object]:
    level = candidate.level
    return {
        "v": 1,
        "id": str(level_number),
        "level_hash": candidate.level_hash,
        "solution_hash": level.solution_hash,
        "solution_program": core.format_program(candidate.solution),
        "solution_steps": candidate.solution_steps,
        "min_moves_to_exit": candidate.min_moves_to_exit,
        "min_direction_types_to_exit": candidate.min_direction_types_to_exit,
        "generator": {
            "mode": "v5_cegis_adversarial",
            "seed": candidate.level_seed,
            "attempts_used": candidate.attempts_used,
            "series_config": series_config,
            "adversary": adversary_config,
            "width": level.width,
            "height": level.height,
            "program_limit": level.program_limit,
            "execution_limit": level.execution_limit,
            "generation_execution_limit": candidate.generation_execution_limit,
            "instruction_coverage": round(candidate.instruction_coverage, 4),
            "visited_cell_count": candidate.visited_cell_count,
            "spread_ratio": round(candidate.spread_ratio, 4),
            "direction_types_used": candidate.direction_types_used,
            "sense_true": candidate.sense_true,
            "sense_false": candidate.sense_false,
            "jump_exec_count": candidate.jump_exec_count,
            "sealed_unreachable_cells": candidate.sealed_unreachable_cells,
            "texture_cleanup_attempts": candidate.texture_cleanup_attempts,
            "texture_flips_applied": candidate.texture_flips_applied,
            "checkerboard_2x2": candidate.checkerboard_2x2,
            "density_driver_percent": round(candidate.density_driver_percent, 2),
            "target_density_percent": round(candidate.target_density_percent, 2),
            "final_density_percent": round(candidate.final_density_percent, 2),
            "blueprint_states": candidate.blueprint_states,
            "blueprint_next_states": candidate.blueprint_next_states,
            "counterexample_pool_size": candidate.counterexample_pool_size,
            "adversary_evaluations": candidate.adversary_evaluations,
            "adversary_execution_limit": candidate.adversary_max_steps_used,
            "score": round(candidate.score, 3),
        },
        "created_at": timestamp_now_utc(),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "V5 CEGIS+adversarial generator. Synthesizes candidate levels like V3, "
            "then runs a short-program adversary. Found counterexample programs are "
            "added to a per-level pool and must fail on future candidates."
        )
    )
    parser.add_argument("level_number", type=int, help="Generate exactly this level number.")
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
        "--progressive-total-levels",
        type=int,
        default=None,
        help="Total level count for progression curves (default: max_level).",
    )

    parser.add_argument("--min-program-length", type=int, default=20, help="Minimum hidden-solution length.")
    parser.add_argument("--max-program-length", type=int, default=50, help="Maximum hidden-solution length.")
    parser.add_argument("--program-cycles", type=float, default=2.0, help="Program-length oscillation cycles.")
    parser.add_argument("--program-phase", type=float, default=0.0, help="Program-length phase in radians.")
    parser.add_argument("--program-noise", type=float, default=1.5, help="Program-length noise amplitude.")
    parser.add_argument("--program-slack", type=int, default=0, help="Extra instructions above hidden solution.")

    parser.add_argument("--min-density", type=float, default=20.0, help="Minimum blocked-cell density percent.")
    parser.add_argument("--max-density", type=float, default=50.0, help="Maximum blocked-cell density percent.")
    parser.add_argument("--density-cycles", type=float, default=1.5, help="Density oscillation cycles.")
    parser.add_argument("--density-phase", type=float, default=1.2, help="Density oscillation phase in radians.")
    parser.add_argument("--density-noise", type=float, default=2.5, help="Density noise amplitude in percent.")
    parser.add_argument(
        "--reachable-open-scale",
        type=float,
        default=0.55,
        help="Scale applied to (1-density) to choose connected reachable open area before sealing.",
    )
    parser.add_argument("--min-reachable-open-ratio", type=float, default=0.14, help="Min reachable open ratio.")
    parser.add_argument("--max-reachable-open-ratio", type=float, default=0.34, help="Max reachable open ratio.")

    parser.add_argument("--center-jitter", type=int, default=8, help="Max center jitter for start position.")
    parser.add_argument("--state-visit-cap", type=int, default=40, help="Max guided visits per state tuple.")
    parser.add_argument("--generation-execution-limit", type=int, default=0, help="Generation simulation cap (0=auto).")

    parser.add_argument("--min-instruction-coverage", type=float, default=0.86, help="Min executed instruction coverage.")
    parser.add_argument("--min-route-spread", type=float, default=0.08, help="Min route spread ratio.")
    parser.add_argument("--min-visited-size-factor", type=float, default=0.5, help="Min visited threshold = size*factor.")
    parser.add_argument("--min-steps-per-size", type=float, default=1.5, help="Min steps threshold = size*factor.")
    parser.add_argument("--min-solution-direction-types", type=int, default=3, help="Min direction types used by solution.")
    parser.add_argument("--max-straight-run", type=int, default=0, help="Reject if hidden solution has straight run >= N.")
    parser.add_argument("--min-direction-types-to-exit", type=int, default=2, help="Min direction types needed to exit.")

    parser.add_argument("--best-of", type=int, default=3, help="Valid candidates required per level.")
    parser.add_argument("--candidate-attempts", type=int, default=500, help="Max candidate attempts per level (0=infinite).")

    parser.add_argument(
        "--elim-from-solution-steps",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Set final elim to hidden solution steps (default: true).",
    )
    parser.add_argument(
        "--seal-unreachable",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Seal unreachable open cells before writing level (default: true).",
    )
    parser.add_argument(
        "--texture-cleanup",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Apply checkerboard-reduction cleanup flips on non-required cells (default: true).",
    )
    parser.add_argument(
        "--texture-cleanup-attempts",
        type=int,
        default=0,
        help="Texture cleanup flip attempts (0=auto from size and scale).",
    )
    parser.add_argument(
        "--texture-cleanup-scale",
        type=float,
        default=0.05,
        help="Auto cleanup attempts scale: round(width*height*scale) when attempts is 0.",
    )

    parser.add_argument("--adversary-min-program-length", type=int, default=2, help="Min adversary program length.")
    parser.add_argument("--adversary-max-program-length", type=int, default=10, help="Max adversary program length.")
    parser.add_argument("--adversary-total-evals", type=int, default=180, help="Total adversary simulations per candidate.")
    parser.add_argument("--adversary-min-evals-per-length", type=int, default=12, help="Min evals allocated to each length.")
    parser.add_argument("--adversary-population", type=int, default=20, help="Adversary population size.")
    parser.add_argument("--adversary-mutation-rate", type=float, default=0.82, help="Adversary mutation probability.")
    parser.add_argument("--adversary-crossover-rate", type=float, default=0.60, help="Adversary crossover probability.")
    parser.add_argument("--adversary-jump-span", type=int, default=12, help="Max jump span used by adversary programs.")
    parser.add_argument("--adversary-execution-limit", type=int, default=0, help="Adversary simulation limit (0=auto).")
    parser.add_argument(
        "--adversary-execution-scale",
        type=float,
        default=0.08,
        help="Auto adversary step cap scale: width*height*scale when explicit limit is 0.",
    )
    parser.add_argument(
        "--counterexample-pool-limit",
        type=int,
        default=64,
        help="Max adversarial counterexample programs remembered per level.",
    )

    parser.add_argument("--seed", type=int, default=None, help="Batch seed (default: random).")
    parser.add_argument("--verbose", action="store_true", help="Show live progress and reject counters.")
    return parser


def validate_args(args: argparse.Namespace) -> tuple[bool, str]:
    if args.start_level < 1:
        return False, "--start-level must be >= 1"
    if args.max_level < args.start_level:
        return False, "max_level must be >= --start-level"
    if args.size is not None and args.size < 2:
        return False, "--size must be >= 2"
    if args.min_size < 2 or args.max_size < 2:
        return False, "--min-size and --max-size must be >= 2"
    if args.max_size < args.min_size:
        return False, "--max-size must be >= --min-size"
    if args.min_program_length < 20:
        return False, "--min-program-length must be >= 20"
    if args.max_program_length < args.min_program_length:
        return False, "--max-program-length must be >= --min-program-length"
    if args.max_program_length > core.MAX_PROGRAM_LIMIT:
        return False, f"--max-program-length must be <= {core.MAX_PROGRAM_LIMIT}"
    if args.program_slack < 0:
        return False, "--program-slack must be >= 0"
    if args.min_density < 0.0 or args.max_density > 100.0 or args.max_density < args.min_density:
        return False, "density bounds must satisfy 0 <= min <= max <= 100"
    if args.reachable_open_scale <= 0.0:
        return False, "--reachable-open-scale must be > 0"
    if args.min_reachable_open_ratio <= 0.0 or args.min_reachable_open_ratio > 1.0:
        return False, "--min-reachable-open-ratio must be in (0,1]"
    if args.max_reachable_open_ratio <= 0.0 or args.max_reachable_open_ratio > 1.0:
        return False, "--max-reachable-open-ratio must be in (0,1]"
    if args.max_reachable_open_ratio < args.min_reachable_open_ratio:
        return False, "--max-reachable-open-ratio must be >= --min-reachable-open-ratio"
    if args.min_instruction_coverage <= 0.0 or args.min_instruction_coverage > 1.0:
        return False, "--min-instruction-coverage must be in (0,1]"
    if args.min_route_spread <= 0.0 or args.min_route_spread > 1.0:
        return False, "--min-route-spread must be in (0,1]"
    if args.min_visited_size_factor < 0.0:
        return False, "--min-visited-size-factor must be >= 0"
    if args.min_steps_per_size < 0.0:
        return False, "--min-steps-per-size must be >= 0"
    if args.min_solution_direction_types < 1 or args.min_solution_direction_types > 4:
        return False, "--min-solution-direction-types must be between 1 and 4"
    if args.min_direction_types_to_exit < 1 or args.min_direction_types_to_exit > 4:
        return False, "--min-direction-types-to-exit must be between 1 and 4"
    if args.max_straight_run < 0:
        return False, "--max-straight-run must be >= 0"
    if args.state_visit_cap < 1:
        return False, "--state-visit-cap must be >= 1"
    if args.best_of < 1:
        return False, "--best-of must be >= 1"
    if args.candidate_attempts < 0:
        return False, "--candidate-attempts must be >= 0"
    if args.candidate_attempts > 0 and args.best_of > args.candidate_attempts:
        return False, "--best-of cannot exceed --candidate-attempts when finite"
    if args.texture_cleanup_attempts < 0:
        return False, "--texture-cleanup-attempts must be >= 0"
    if args.texture_cleanup_scale < 0.0:
        return False, "--texture-cleanup-scale must be >= 0"

    if args.adversary_min_program_length < 1:
        return False, "--adversary-min-program-length must be >= 1"
    if args.adversary_max_program_length < args.adversary_min_program_length:
        return False, "--adversary-max-program-length must be >= --adversary-min-program-length"
    if args.adversary_total_evals < 0:
        return False, "--adversary-total-evals must be >= 0"
    if args.adversary_min_evals_per_length < 1:
        return False, "--adversary-min-evals-per-length must be >= 1"
    if args.adversary_population < 2:
        return False, "--adversary-population must be >= 2"
    if args.adversary_mutation_rate < 0.0 or args.adversary_mutation_rate > 1.0:
        return False, "--adversary-mutation-rate must be in [0,1]"
    if args.adversary_crossover_rate < 0.0 or args.adversary_crossover_rate > 1.0:
        return False, "--adversary-crossover-rate must be in [0,1]"
    if args.adversary_jump_span < 2:
        return False, "--adversary-jump-span must be >= 2"
    if args.adversary_execution_limit < 0:
        return False, "--adversary-execution-limit must be >= 0"
    if args.adversary_execution_scale <= 0.0:
        return False, "--adversary-execution-scale must be > 0"
    if args.counterexample_pool_limit < 0:
        return False, "--counterexample-pool-limit must be >= 0"
    return True, ""


def main(argv: list[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.level_number < 1:
        print("Error: level_number must be >= 1.", file=sys.stderr)
        return 2
    args.start_level = args.level_number
    args.max_level = args.level_number

    ok, message = validate_args(args)
    if not ok:
        print(f"Error: {message}.", file=sys.stderr)
        return 2

    if args.progressive_total_levels is None:
        args.progressive_total_levels = args.max_level
    if args.progressive_total_levels < args.max_level:
        print("Error: --progressive-total-levels must be >= max_level.", file=sys.stderr)
        return 2

    try:
        v3 = load_v3_module()
    except Exception as exc:  # noqa: BLE001
        print(f"Error: could not load v3 helpers: {exc}", file=sys.stderr)
        return 2

    if args.seed is None:
        args.seed = random.SystemRandom().randrange(0, 2**63)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    args.solution_dir.mkdir(parents=True, exist_ok=True)

    batch_rng = random.Random(args.seed)
    show_live_progress = args.verbose and sys.stdout.isatty()
    attempts_text = "inf" if args.candidate_attempts == 0 else str(args.candidate_attempts)
    attempts_width = len(attempts_text) if args.candidate_attempts > 0 else 8

    print(
        f"Generating V5-CEGIS levels {args.start_level}..{args.max_level} "
        f"(seed={args.seed}, out={args.out_dir}, solutions={args.solution_dir}, "
        f"size={'fixed '+str(args.size) if args.size is not None else str(args.min_size)+'->'+str(args.max_size)}, "
        f"program_len={args.min_program_length}..{args.max_program_length}, "
        f"density={args.min_density:.1f}%..{args.max_density:.1f}%, "
        f"best_of={args.best_of}, candidate_attempts={attempts_text}, "
        f"adversary_len={args.adversary_min_program_length}..{args.adversary_max_program_length}, "
        f"adversary_evals={args.adversary_total_evals}, "
        f"counterexample_pool_limit={args.counterexample_pool_limit}, "
        f"elim_from_solution_steps={'on' if args.elim_from_solution_steps else 'off'}, "
        f"seal_unreachable={'on' if args.seal_unreachable else 'off'}, "
        f"texture_cleanup={'on' if args.texture_cleanup else 'off'})"
    )

    series_config = {
        "size": args.size,
        "min_size": args.min_size,
        "max_size": args.max_size,
        "progressive_total_levels": args.progressive_total_levels,
        "min_program_length": args.min_program_length,
        "max_program_length": args.max_program_length,
        "program_cycles": args.program_cycles,
        "program_phase": args.program_phase,
        "program_noise": args.program_noise,
        "program_slack": args.program_slack,
        "min_density": args.min_density,
        "max_density": args.max_density,
        "density_cycles": args.density_cycles,
        "density_phase": args.density_phase,
        "density_noise": args.density_noise,
        "reachable_open_scale": args.reachable_open_scale,
        "min_reachable_open_ratio": args.min_reachable_open_ratio,
        "max_reachable_open_ratio": args.max_reachable_open_ratio,
        "center_jitter": args.center_jitter,
        "state_visit_cap": args.state_visit_cap,
        "min_instruction_coverage": args.min_instruction_coverage,
        "min_route_spread": args.min_route_spread,
        "min_visited_size_factor": args.min_visited_size_factor,
        "min_steps_per_size": args.min_steps_per_size,
        "min_solution_direction_types": args.min_solution_direction_types,
        "min_direction_types_to_exit": args.min_direction_types_to_exit,
        "max_straight_run": args.max_straight_run,
        "texture_cleanup": args.texture_cleanup,
        "texture_cleanup_attempts": args.texture_cleanup_attempts,
        "texture_cleanup_scale": args.texture_cleanup_scale,
    }
    adversary_config = {
        "min_program_length": args.adversary_min_program_length,
        "max_program_length": args.adversary_max_program_length,
        "total_evals": args.adversary_total_evals,
        "min_evals_per_length": args.adversary_min_evals_per_length,
        "population": args.adversary_population,
        "mutation_rate": args.adversary_mutation_rate,
        "crossover_rate": args.adversary_crossover_rate,
        "jump_span": args.adversary_jump_span,
        "execution_limit": args.adversary_execution_limit,
        "execution_scale": args.adversary_execution_scale,
        "counterexample_pool_limit": args.counterexample_pool_limit,
    }

    for level_number in range(args.start_level, args.max_level + 1):
        size = v3.choose_size(level_number, args)
        target_program_length = v3.choose_target_program_length(level_number, args, batch_rng)
        density_driver_percent = v3.choose_density_percent(level_number, args, batch_rng)
        target_open_ratio = v3.choose_target_open_ratio(density_driver_percent, args)
        target_final_density_percent = 100.0 * (1.0 - target_open_ratio)

        min_steps_required = max(40, int(round(size * args.min_steps_per_size)))
        min_visited_required = max(12, int(round(size * args.min_visited_size_factor)))

        progress_width = 0
        reject_counts: dict[str, int] = {}
        candidate_pool: list[Candidate] = []
        attempts_used = 0
        counterexamples: list[list[core.Instruction]] = []
        counterexample_hashes: set[str] = set()

        def register_reject(code: str) -> None:
            nonlocal progress_width
            reject_counts[code] = reject_counts.get(code, 0) + 1
            if show_live_progress:
                attempts_value = f"{attempts_used:>{attempts_width}d}"
                progress_width = update_progress_line(
                    f"Level {level_number}/{args.max_level}: attempts={attempts_value}/{attempts_text}, "
                    f"best_of={len(candidate_pool)}/{args.best_of}, cex={len(counterexamples)}, "
                    f"status=rejected({code}), {format_reject_counts(reject_counts)}",
                    progress_width,
                    show_live_progress,
                )

        if args.verbose:
            print(
                f"Level {level_number} constraints: size={size}x{size}, "
                f"target_program_len={target_program_length}, density_driver={density_driver_percent:.1f}%, "
                f"target_final_density={target_final_density_percent:.1f}%, "
                f"min_steps={min_steps_required}, min_visited={min_visited_required}, "
                f"min_spread={args.min_route_spread:.2f}, min_solution_dir_types={args.min_solution_direction_types}, "
                f"min_exit_dir_types={args.min_direction_types_to_exit}, best_of={args.best_of}, "
                f"candidate_attempts={attempts_text}, "
                f"adv_len={args.adversary_min_program_length}..{args.adversary_max_program_length}, "
                f"adv_evals={args.adversary_total_evals}, cex_limit={args.counterexample_pool_limit}"
            )

        while len(candidate_pool) < args.best_of and (
            args.candidate_attempts == 0 or attempts_used < args.candidate_attempts
        ):
            attempts_used += 1
            level_seed = batch_rng.randrange(0, 2**63)
            rng = random.Random(level_seed)

            start_x, start_y = v3.choose_start(size, args.center_jitter, rng)
            blueprint = v3.generate_blueprint(
                target_program_length=target_program_length,
                min_program_length=args.min_program_length,
                max_program_length=args.max_program_length,
                rng=rng,
            )
            if blueprint is None:
                register_reject("pb")
                continue

            program = blueprint.program
            if core.has_meaningless_jump_instruction(program):
                register_reject("mj")
                continue

            generation_execution_limit = v3.choose_generation_execution_limit(
                size,
                len(program),
                args.generation_execution_limit,
            )
            trace = v3.build_guided_trace(
                blueprint=blueprint,
                start_x=start_x,
                start_y=start_y,
                size=size,
                min_steps_required=min_steps_required,
                max_steps=generation_execution_limit,
                state_visit_cap=args.state_visit_cap,
                rng=rng,
            )
            if trace is None:
                register_reject("ct")
                continue

            if trace.steps < min_steps_required:
                register_reject("ms")
                continue
            if trace.jump_exec_count == 0 or (trace.sense_true + trace.sense_false) == 0:
                register_reject("js")
                continue
            if trace.sense_true == 0 or trace.sense_false == 0:
                register_reject("sb")
                continue

            trace_coverage = len(trace.executed_pcs) / float(len(program))
            if trace_coverage < args.min_instruction_coverage:
                register_reject("ux")
                continue

            trace_direction_types = sum(1 for count in trace.direction_move_counts if count > 0)
            if trace_direction_types < args.min_solution_direction_types:
                register_reject("dv")
                continue

            trace_spread = v3.route_spread_ratio(size, trace.visited_cells)
            if trace_spread < args.min_route_spread:
                register_reject("sp")
                continue
            if len(trace.visited_cells) < min_visited_required:
                register_reject("vc")
                continue

            board = v3.build_board(
                size=size,
                target_open_ratio=target_open_ratio,
                requirements=trace.requirements,
                start_x=start_x,
                start_y=start_y,
                rng=rng,
            )
            if board is None:
                register_reject("dn")
                continue

            texture_cleanup_attempts_used = 0
            texture_flips_applied = 0
            if args.texture_cleanup:
                texture_cleanup_attempts_used = (
                    args.texture_cleanup_attempts
                    if args.texture_cleanup_attempts > 0
                    else max(1, int(round((size * size) * args.texture_cleanup_scale)))
                )
                texture_flips_applied = v3.apply_texture_cleanup(
                    board=board,
                    requirements=trace.requirements,
                    start_x=start_x,
                    start_y=start_y,
                    attempts=texture_cleanup_attempts_used,
                    rng=rng,
                )

            program_limit = clamp_int(
                len(program) + args.program_slack,
                len(program),
                core.MAX_PROGRAM_LIMIT,
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
                program_limit=program_limit,
                execution_limit=generation_execution_limit,
                solution_hash=None,
            )

            run_result, visited_cells, executed_pcs, direction_counts = v3.simulate_with_trace(
                level=level,
                program=program,
                max_steps=generation_execution_limit,
            )
            if run_result.outcome != "escape":
                register_reject("ne")
                continue

            if args.max_straight_run > 0 and core.has_straight_run_at_least(
                level,
                program,
                args.max_straight_run,
                generation_execution_limit,
            ):
                register_reject("sr")
                continue

            has_turn_cancel, has_dead_instruction = core.analyze_execution_path(
                level,
                program,
                generation_execution_limit,
            )
            if has_turn_cancel:
                register_reject("tc")
                continue
            if has_dead_instruction:
                register_reject("ux")
                continue

            if core.has_easy_two_direction_program(level):
                register_reject("pl")
                continue

            min_moves_to_exit = core.minimum_moves_to_exit(level)
            if min_moves_to_exit is None:
                register_reject("np")
                continue
            min_direction_types_to_exit = core.minimum_distinct_directions_to_exit(level)
            if min_direction_types_to_exit is None:
                register_reject("np")
                continue
            if min_direction_types_to_exit < args.min_direction_types_to_exit:
                register_reject("md")
                continue

            solution_hash = core.compute_program_hash(program)
            level.solution_hash = solution_hash

            sealed_unreachable_cells = 0
            if args.seal_unreachable:
                sealed_unreachable_cells = core.seal_unreachable_cells(level)
                post_seal_run = core.simulate_program(level, program, generation_execution_limit)
                if post_seal_run.outcome != "escape":
                    register_reject("ne")
                    continue
                run_result = post_seal_run
                min_moves_to_exit = core.minimum_moves_to_exit(level)
                if min_moves_to_exit is None:
                    register_reject("np")
                    continue
                min_direction_types_to_exit = core.minimum_distinct_directions_to_exit(level)
                if min_direction_types_to_exit is None:
                    register_reject("np")
                    continue
                if min_direction_types_to_exit < args.min_direction_types_to_exit:
                    register_reject("md")
                    continue

            if args.elim_from_solution_steps:
                level.execution_limit = max(1, run_result.steps)

            adversary_max_steps = choose_adversary_execution_limit(level, args)
            if show_live_progress:
                attempts_value = f"{attempts_used:>{attempts_width}d}"
                progress_width = update_progress_line(
                    f"Level {level_number}/{args.max_level}: attempts={attempts_value}/{attempts_text}, "
                    f"best_of={len(candidate_pool)}/{args.best_of}, cex={len(counterexamples)}, "
                    f"status=checking(cx/ad), {format_reject_counts(reject_counts)}",
                    progress_width,
                    show_live_progress,
                )
            if counterexamples and run_known_counterexamples(level, counterexamples, adversary_max_steps):
                register_reject("cx")
                continue

            attack = search_adversarial_program(
                level=level,
                args=args,
                rng=rng,
                blocked_hashes=counterexample_hashes,
            )
            if attack.program is not None:
                register_reject("ad")
                attack_hash = core.compute_program_hash(attack.program)
                if (
                    args.counterexample_pool_limit > 0
                    and attack_hash not in counterexample_hashes
                    and len(counterexamples) < args.counterexample_pool_limit
                ):
                    counterexample_hashes.add(attack_hash)
                    counterexamples.append(attack.program)
                continue

            final_density_percent = 100.0 * core.block_count(level.board) / float(size * size)
            checkerboard_2x2 = v3.checkerboard_2x2_count(level.board)
            level_text = core.format_level(level)
            level_hash = core.compute_level_hash(level)
            instruction_coverage = len(executed_pcs) / float(len(program))
            spread_ratio = v3.route_spread_ratio(size, visited_cells)
            direction_types_used = sum(1 for count in direction_counts if count > 0)
            score = (
                run_result.steps
                + min_moves_to_exit * 5.0
                + len(visited_cells) * 1.7
                + spread_ratio * (size * size) * 0.22
                + direction_types_used * 420.0
                + instruction_coverage * 1000.0
                + len(counterexamples) * 18.0
            )

            candidate_pool.append(
                Candidate(
                    level_seed=level_seed,
                    level=level,
                    level_text=level_text,
                    level_hash=level_hash,
                    solution=program,
                    solution_steps=run_result.steps,
                    min_moves_to_exit=min_moves_to_exit,
                    min_direction_types_to_exit=min_direction_types_to_exit,
                    instruction_coverage=instruction_coverage,
                    visited_cell_count=len(visited_cells),
                    spread_ratio=spread_ratio,
                    direction_types_used=direction_types_used,
                    sense_true=trace.sense_true,
                    sense_false=trace.sense_false,
                    jump_exec_count=trace.jump_exec_count,
                    generation_execution_limit=generation_execution_limit,
                    sealed_unreachable_cells=sealed_unreachable_cells,
                    texture_cleanup_attempts=texture_cleanup_attempts_used,
                    texture_flips_applied=texture_flips_applied,
                    checkerboard_2x2=checkerboard_2x2,
                    density_driver_percent=density_driver_percent,
                    target_density_percent=target_final_density_percent,
                    final_density_percent=final_density_percent,
                    blueprint_states=blueprint.state_count,
                    blueprint_next_states=list(blueprint.next_states),
                    score=score,
                    attempts_used=attempts_used,
                    counterexample_pool_size=len(counterexamples),
                    adversary_evaluations=attack.evaluations,
                    adversary_max_steps_used=attack.max_steps_used,
                )
            )

            if show_live_progress:
                attempts_value = f"{attempts_used:>{attempts_width}d}"
                best_score = max(item.score for item in candidate_pool)
                progress_width = update_progress_line(
                    f"Level {level_number}/{args.max_level}: attempts={attempts_value}/{attempts_text}, "
                    f"best_of={len(candidate_pool)}/{args.best_of}, cex={len(counterexamples)}, "
                    f"best_score={best_score:.1f}, status=candidate_ok, {format_reject_counts(reject_counts)}",
                    progress_width,
                    show_live_progress,
                )

        clear_progress_line(progress_width, show_live_progress)

        if len(candidate_pool) < args.best_of:
            print(
                f"Error generating level {level_number}: found {len(candidate_pool)}/{args.best_of} "
                f"valid candidates after {attempts_used} attempts "
                f"(cex_pool={len(counterexamples)}, rejects: {format_reject_counts(reject_counts)}).",
                file=sys.stderr,
            )
            return 2

        chosen = max(candidate_pool, key=lambda item: item.score)
        solution_payload = build_solution_payload(
            level_number=level_number,
            candidate=chosen,
            series_config=series_config,
            adversary_config=adversary_config,
        )

        level_path = args.out_dir / f"{level_number}.level"
        solution_path = args.solution_dir / f"{level_number}.solution.json"
        level_path.write_text(chosen.level_text + "\n", encoding="utf-8")
        solution_path.write_text(
            json.dumps(solution_payload, indent=2, sort_keys=False) + "\n",
            encoding="utf-8",
        )

        print(
            f"Level {level_number}: ok "
            f"(size={chosen.level.width}x{chosen.level.height}, plim={chosen.level.program_limit}, "
            f"elim={chosen.level.execution_limit}, attempts={chosen.attempts_used}, "
            f"solution_steps={chosen.solution_steps}, visited={chosen.visited_cell_count}, "
            f"spread={chosen.spread_ratio:.3f}, direction_types_used={chosen.direction_types_used}, "
            f"density_driver={chosen.density_driver_percent:.1f}%, density_target={chosen.target_density_percent:.1f}%, "
            f"density_final={chosen.final_density_percent:.1f}%, cb2x2={chosen.checkerboard_2x2}, "
            f"texture_flips={chosen.texture_flips_applied}/{chosen.texture_cleanup_attempts}, "
            f"min_moves_to_exit={chosen.min_moves_to_exit}, min_direction_types_to_exit={chosen.min_direction_types_to_exit}, "
            f"blueprint_states={chosen.blueprint_states}, sealed_unreachable={chosen.sealed_unreachable_cells}, "
            f"cex_pool={chosen.counterexample_pool_size}, adversary_evals={chosen.adversary_evaluations}, "
            f"score={chosen.score:.1f})"
        )
        if args.verbose:
            print(f"Level {level_number} solution_program: {core.format_program(chosen.solution)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
