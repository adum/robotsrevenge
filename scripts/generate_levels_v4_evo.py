#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
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
    "pb",  # blueprint build failed
    "mj",  # meaningless jump
    "ct",  # trace failed
    "ms",  # minimum steps not met
    "js",  # jump/sense usage missing
    "sb",  # missing S-branch split
    "ux",  # low instruction usage / dead path
    "dv",  # low solution direction diversity
    "sp",  # route spread too low
    "vc",  # visited cells too low
    "dn",  # density/open ratio infeasible
    "ne",  # hidden solution did not escape
    "sr",  # straight-run limit
    "tc",  # turn-cancel pattern
    "pl",  # easy short program exists
    "np",  # no movement-only path
    "md",  # min direction types to exit not met
)


@dataclass
class Genome:
    state_count: int
    jump_bias: float
    rewire_ratio: float
    turn_left_bias: float
    open_scale: float
    open_min_ratio: float
    open_max_ratio: float
    center_jitter: int
    state_visit_cap: int


@dataclass
class Candidate:
    level_seed: int
    attempt_index: int
    fitness: float
    raw_score: float
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
    target_density_percent: float
    density_driver_percent: float
    final_density_percent: float
    novelty: float
    genome: Genome


@dataclass
class EvalOutcome:
    genome: Genome
    fitness: float
    reject_code: str | None
    candidate: Candidate | None


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


def level_progress(level_number: int, total_levels: int) -> float:
    if total_levels <= 1:
        return 0.0
    return clamp_float((level_number - 1) / float(total_levels - 1), 0.0, 1.0)


def choose_size(level_number: int, args: argparse.Namespace) -> int:
    if args.size is not None:
        return args.size
    t = smoothstep(level_progress(level_number, args.progressive_total_levels))
    raw = args.min_size + (args.max_size - args.min_size) * t
    return clamp_int(int(round(raw)), args.min_size, args.max_size)


def choose_target_program_length(level_number: int, args: argparse.Namespace, rng: random.Random) -> int:
    t = level_progress(level_number, args.progressive_total_levels)
    center = 0.5 * (args.min_program_length + args.max_program_length)
    amplitude = 0.5 * (args.max_program_length - args.min_program_length)
    wave = math.sin(2.0 * math.pi * args.program_cycles * t + args.program_phase)
    noise = rng.uniform(-args.program_noise, args.program_noise)
    target = int(round(center + amplitude * wave + noise))
    return clamp_int(target, args.min_program_length, args.max_program_length)


def choose_density_percent(level_number: int, args: argparse.Namespace, rng: random.Random) -> float:
    t = level_progress(level_number, args.progressive_total_levels)
    center = 0.5 * (args.min_density + args.max_density)
    amplitude = 0.5 * (args.max_density - args.min_density)
    wave_primary = math.sin(2.0 * math.pi * args.density_cycles * t + args.density_phase)
    wave_secondary = 0.35 * math.sin(2.0 * math.pi * (args.density_cycles * 0.53) * t + 0.7)
    noise = rng.uniform(-args.density_noise, args.density_noise)
    density = center + amplitude * wave_primary + amplitude * wave_secondary + noise
    return clamp_float(density, args.min_density, args.max_density)


def ordered_state_counts(min_program_length: int, max_program_length: int) -> list[int]:
    min_states = max(5, math.ceil(min_program_length / 4.0))
    max_states = max(min_states, math.floor(max_program_length / 4.0))
    return list(range(min_states, max_states + 1))


def nearest_coprime_step(state_count: int, desired_step: int) -> int:
    candidates = [step for step in range(2, state_count) if math.gcd(step, state_count) == 1]
    if not candidates:
        candidates = [1]
    return min(candidates, key=lambda step: abs(step - desired_step))


def build_next_states(state_count: int, jump_bias: float, rewire_ratio: float, rng: random.Random) -> tuple[int, ...]:
    desired_step = clamp_int(int(round(jump_bias * max(2, state_count - 1))), 2, max(2, state_count - 1))
    step = nearest_coprime_step(state_count, desired_step)

    cycle: list[int] = [0]
    for _ in range(1, state_count):
        cycle.append((cycle[-1] + step) % state_count)

    rewire_count = clamp_int(int(round(rewire_ratio * state_count)), 0, max(0, state_count - 2))
    for _ in range(rewire_count):
        i = rng.randrange(0, state_count)
        j = rng.randrange(0, state_count)
        if i == j:
            continue
        cycle[i], cycle[j] = cycle[j], cycle[i]

    next_states = [0 for _ in range(state_count)]
    for index, state in enumerate(cycle):
        next_states[state] = cycle[(index + 1) % state_count]
    return tuple(next_states)


def build_turns(state_count: int, turn_left_bias: float, rng: random.Random) -> tuple[str, ...]:
    turns = ["L" if rng.random() < turn_left_bias else "R" for _ in range(state_count)]
    if all(turn == "L" for turn in turns):
        turns[rng.randrange(0, state_count)] = "R"
    if all(turn == "R" for turn in turns):
        turns[rng.randrange(0, state_count)] = "L"
    return tuple(turns)


def random_genome(
    size: int,
    target_program_length: int,
    args: argparse.Namespace,
    rng: random.Random,
) -> Genome:
    state_options = ordered_state_counts(args.min_program_length, args.max_program_length)
    target_states = clamp_int(int(round(target_program_length / 4.0)), min(state_options), max(state_options))
    state_count = state_options[rng.randrange(0, len(state_options))]
    if rng.random() < 0.7:
        jitter = rng.randint(-2, 2)
        state_count = clamp_int(target_states + jitter, min(state_options), max(state_options))

    center_jitter_cap = max(2, size // 6)
    return Genome(
        state_count=state_count,
        jump_bias=rng.uniform(0.2, 0.85),
        rewire_ratio=rng.uniform(0.0, 0.35),
        turn_left_bias=rng.uniform(0.2, 0.8),
        open_scale=rng.uniform(0.4, 0.95),
        open_min_ratio=rng.uniform(0.10, 0.22),
        open_max_ratio=rng.uniform(0.22, 0.42),
        center_jitter=rng.randint(0, min(center_jitter_cap, max(0, args.center_jitter + center_jitter_cap // 2))),
        state_visit_cap=rng.randint(max(12, args.state_visit_cap // 2), max(24, args.state_visit_cap * 2)),
    )


def clamp_genome(genome: Genome, size: int, args: argparse.Namespace) -> Genome:
    state_options = ordered_state_counts(args.min_program_length, args.max_program_length)
    min_state = min(state_options)
    max_state = max(state_options)
    center_jitter_cap = max(2, size // 6)

    state_count = clamp_int(genome.state_count, min_state, max_state)
    jump_bias = clamp_float(genome.jump_bias, 0.05, 0.95)
    rewire_ratio = clamp_float(genome.rewire_ratio, 0.0, 0.6)
    turn_left_bias = clamp_float(genome.turn_left_bias, 0.05, 0.95)
    open_scale = clamp_float(genome.open_scale, 0.2, 1.2)
    open_min_ratio = clamp_float(genome.open_min_ratio, 0.05, 0.45)
    open_max_ratio = clamp_float(genome.open_max_ratio, open_min_ratio + 0.02, 0.60)
    center_jitter = clamp_int(genome.center_jitter, 0, center_jitter_cap)
    state_visit_cap = clamp_int(genome.state_visit_cap, 8, 160)

    return Genome(
        state_count=state_count,
        jump_bias=jump_bias,
        rewire_ratio=rewire_ratio,
        turn_left_bias=turn_left_bias,
        open_scale=open_scale,
        open_min_ratio=open_min_ratio,
        open_max_ratio=open_max_ratio,
        center_jitter=center_jitter,
        state_visit_cap=state_visit_cap,
    )


def crossover_genomes(parent_a: Genome, parent_b: Genome, rng: random.Random) -> Genome:
    def pick_int(a: int, b: int) -> int:
        if rng.random() < 0.5:
            return a
        return b

    def blend_float(a: float, b: float) -> float:
        t = rng.uniform(0.0, 1.0)
        return a * (1.0 - t) + b * t

    return Genome(
        state_count=pick_int(parent_a.state_count, parent_b.state_count),
        jump_bias=blend_float(parent_a.jump_bias, parent_b.jump_bias),
        rewire_ratio=blend_float(parent_a.rewire_ratio, parent_b.rewire_ratio),
        turn_left_bias=blend_float(parent_a.turn_left_bias, parent_b.turn_left_bias),
        open_scale=blend_float(parent_a.open_scale, parent_b.open_scale),
        open_min_ratio=blend_float(parent_a.open_min_ratio, parent_b.open_min_ratio),
        open_max_ratio=blend_float(parent_a.open_max_ratio, parent_b.open_max_ratio),
        center_jitter=pick_int(parent_a.center_jitter, parent_b.center_jitter),
        state_visit_cap=pick_int(parent_a.state_visit_cap, parent_b.state_visit_cap),
    )


def mutate_genome(genome: Genome, size: int, args: argparse.Namespace, rng: random.Random) -> Genome:
    mutated = copy.deepcopy(genome)

    if rng.random() < 0.35:
        mutated.state_count += rng.choice([-2, -1, 1, 2])
    if rng.random() < 0.45:
        mutated.jump_bias += rng.uniform(-0.12, 0.12)
    if rng.random() < 0.45:
        mutated.rewire_ratio += rng.uniform(-0.10, 0.10)
    if rng.random() < 0.45:
        mutated.turn_left_bias += rng.uniform(-0.12, 0.12)
    if rng.random() < 0.45:
        mutated.open_scale += rng.uniform(-0.15, 0.15)
    if rng.random() < 0.35:
        mutated.open_min_ratio += rng.uniform(-0.04, 0.04)
    if rng.random() < 0.35:
        mutated.open_max_ratio += rng.uniform(-0.05, 0.05)
    if rng.random() < 0.35:
        mutated.center_jitter += rng.randint(-2, 2)
    if rng.random() < 0.35:
        mutated.state_visit_cap += rng.randint(-8, 8)

    return clamp_genome(mutated, size, args)


def tournament_select(population: list[EvalOutcome], rng: random.Random, tournament_size: int) -> Genome:
    if not population:
        raise RuntimeError("Population is empty")
    best = population[rng.randrange(0, len(population))]
    for _ in range(max(1, tournament_size) - 1):
        contender = population[rng.randrange(0, len(population))]
        if contender.fitness > best.fitness:
            best = contender
    return best.genome


def candidate_feature_vector(candidate: Candidate, size: int) -> tuple[float, float, float, float, float, float]:
    return (
        candidate.solution_steps / float(max(1, size)),
        candidate.spread_ratio,
        candidate.direction_types_used / 4.0,
        candidate.final_density_percent / 100.0,
        len(candidate.solution) / float(core.MAX_PROGRAM_LIMIT),
        candidate.min_moves_to_exit / float(max(1, size)),
    )


def feature_distance(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    return math.sqrt(sum((x - y) * (x - y) for x, y in zip(a, b)))


def novelty_score(
    feature: tuple[float, ...],
    hall_features: list[tuple[float, ...]],
    max_neighbors: int,
) -> float:
    if not hall_features:
        return 0.6
    distances = [feature_distance(feature, other) for other in hall_features]
    distances.sort(reverse=True)
    top = distances[: max(1, max_neighbors)]
    return sum(top) / float(len(top))


def compute_target_open_ratio(density_driver_percent: float, genome: Genome) -> float:
    base_open = 1.0 - (density_driver_percent / 100.0)
    scaled_open = base_open * genome.open_scale
    return clamp_float(scaled_open, genome.open_min_ratio, genome.open_max_ratio)


def evaluate_genome(
    v3,
    genome: Genome,
    level_number: int,
    size: int,
    target_program_length: int,
    density_driver_percent: float,
    min_steps_required: int,
    min_visited_required: int,
    args: argparse.Namespace,
    level_seed: int,
    attempt_index: int,
    hall_features: list[tuple[float, ...]],
) -> EvalOutcome:
    rng = random.Random(level_seed)
    genome = clamp_genome(genome, size, args)

    program_len = genome.state_count * 4
    if program_len < args.min_program_length or program_len > args.max_program_length:
        return EvalOutcome(genome=genome, fitness=-1e9, reject_code="pb", candidate=None)

    next_states = build_next_states(genome.state_count, genome.jump_bias, genome.rewire_ratio, rng)
    turns = build_turns(genome.state_count, genome.turn_left_bias, rng)
    program = v3.build_program(genome.state_count, next_states, turns)

    if core.has_meaningless_jump_instruction(program):
        return EvalOutcome(genome=genome, fitness=-1e9, reject_code="mj", candidate=None)

    start_x, start_y = v3.choose_start(size, genome.center_jitter, rng)
    generation_execution_limit = v3.choose_generation_execution_limit(
        size,
        len(program),
        args.generation_execution_limit,
    )

    blueprint = v3.ProgramBlueprint(
        state_count=genome.state_count,
        next_states=next_states,
        turns=turns,
        program=program,
    )

    trace = v3.build_guided_trace(
        blueprint=blueprint,
        start_x=start_x,
        start_y=start_y,
        size=size,
        min_steps_required=min_steps_required,
        max_steps=generation_execution_limit,
        state_visit_cap=genome.state_visit_cap,
        rng=rng,
    )
    if trace is None:
        return EvalOutcome(genome=genome, fitness=-1e9, reject_code="ct", candidate=None)

    if trace.steps < min_steps_required:
        return EvalOutcome(genome=genome, fitness=-1e9, reject_code="ms", candidate=None)
    if trace.jump_exec_count == 0 or (trace.sense_true + trace.sense_false) == 0:
        return EvalOutcome(genome=genome, fitness=-1e9, reject_code="js", candidate=None)
    if trace.sense_true == 0 or trace.sense_false == 0:
        return EvalOutcome(genome=genome, fitness=-1e9, reject_code="sb", candidate=None)

    trace_coverage = len(trace.executed_pcs) / float(len(program))
    if trace_coverage < args.min_instruction_coverage:
        return EvalOutcome(genome=genome, fitness=-1e9, reject_code="ux", candidate=None)

    trace_direction_types = sum(1 for count in trace.direction_move_counts if count > 0)
    if trace_direction_types < args.min_solution_direction_types:
        return EvalOutcome(genome=genome, fitness=-1e9, reject_code="dv", candidate=None)

    trace_spread = v3.route_spread_ratio(size, trace.visited_cells)
    if trace_spread < args.min_route_spread:
        return EvalOutcome(genome=genome, fitness=-1e9, reject_code="sp", candidate=None)
    if len(trace.visited_cells) < min_visited_required:
        return EvalOutcome(genome=genome, fitness=-1e9, reject_code="vc", candidate=None)

    target_open_ratio = compute_target_open_ratio(density_driver_percent, genome)
    target_final_density_percent = 100.0 * (1.0 - target_open_ratio)

    board = v3.build_board(
        size=size,
        target_open_ratio=target_open_ratio,
        requirements=trace.requirements,
        start_x=start_x,
        start_y=start_y,
        rng=rng,
    )
    if board is None:
        return EvalOutcome(genome=genome, fitness=-1e9, reject_code="dn", candidate=None)

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

    program_limit = clamp_int(len(program) + args.program_slack, len(program), core.MAX_PROGRAM_LIMIT)
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
        return EvalOutcome(genome=genome, fitness=-1e9, reject_code="ne", candidate=None)

    if args.max_straight_run > 0 and core.has_straight_run_at_least(
        level,
        program,
        args.max_straight_run,
        generation_execution_limit,
    ):
        return EvalOutcome(genome=genome, fitness=-1e9, reject_code="sr", candidate=None)

    has_turn_cancel, has_dead_instruction = core.analyze_execution_path(
        level,
        program,
        generation_execution_limit,
    )
    if has_turn_cancel:
        return EvalOutcome(genome=genome, fitness=-1e9, reject_code="tc", candidate=None)
    if has_dead_instruction:
        return EvalOutcome(genome=genome, fitness=-1e9, reject_code="ux", candidate=None)

    if core.has_easy_two_direction_program(level):
        return EvalOutcome(genome=genome, fitness=-1e9, reject_code="pl", candidate=None)

    min_moves_to_exit = core.minimum_moves_to_exit(level)
    if min_moves_to_exit is None:
        return EvalOutcome(genome=genome, fitness=-1e9, reject_code="np", candidate=None)
    min_direction_types_to_exit = core.minimum_distinct_directions_to_exit(level)
    if min_direction_types_to_exit is None:
        return EvalOutcome(genome=genome, fitness=-1e9, reject_code="np", candidate=None)
    if min_direction_types_to_exit < args.min_direction_types_to_exit:
        return EvalOutcome(genome=genome, fitness=-1e9, reject_code="md", candidate=None)

    solution_hash = core.compute_program_hash(program)
    level.solution_hash = solution_hash

    sealed_unreachable_cells = 0
    if args.seal_unreachable:
        sealed_unreachable_cells = core.seal_unreachable_cells(level)
        post_seal_run = core.simulate_program(level, program, generation_execution_limit)
        if post_seal_run.outcome != "escape":
            return EvalOutcome(genome=genome, fitness=-1e9, reject_code="ne", candidate=None)
        run_result = post_seal_run
        min_moves_to_exit = core.minimum_moves_to_exit(level)
        if min_moves_to_exit is None:
            return EvalOutcome(genome=genome, fitness=-1e9, reject_code="np", candidate=None)
        min_direction_types_to_exit = core.minimum_distinct_directions_to_exit(level)
        if min_direction_types_to_exit is None:
            return EvalOutcome(genome=genome, fitness=-1e9, reject_code="np", candidate=None)
        if min_direction_types_to_exit < args.min_direction_types_to_exit:
            return EvalOutcome(genome=genome, fitness=-1e9, reject_code="md", candidate=None)

    if args.elim_from_solution_steps:
        level.execution_limit = max(1, run_result.steps)

    level_text = core.format_level(level)
    level_hash = core.compute_level_hash(level)

    instruction_coverage = len(executed_pcs) / float(len(program))
    spread_ratio = v3.route_spread_ratio(size, visited_cells)
    direction_types_used = sum(1 for count in direction_counts if count > 0)
    final_density_percent = 100.0 * core.block_count(level.board) / float(size * size)
    checkerboard_2x2 = v3.checkerboard_2x2_count(level.board)

    sense_total = max(1, trace.sense_true + trace.sense_false)
    sense_balance = min(trace.sense_true, trace.sense_false) / float(sense_total)
    target_len_penalty = abs(len(program) - target_program_length)
    target_density_penalty = abs(final_density_percent - target_final_density_percent)

    raw_score = (
        run_result.steps
        + min_moves_to_exit * 5.5
        + len(visited_cells) * 1.9
        + spread_ratio * (size * size) * 0.18
        + direction_types_used * 420.0
        + instruction_coverage * 1000.0
        + sense_balance * 320.0
        - target_len_penalty * 28.0
        - target_density_penalty * 6.0
    )

    placeholder = Candidate(
        level_seed=level_seed,
        attempt_index=attempt_index,
        fitness=raw_score,
        raw_score=raw_score,
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
        target_density_percent=target_final_density_percent,
        density_driver_percent=density_driver_percent,
        final_density_percent=final_density_percent,
        novelty=0.0,
        genome=genome,
    )

    feature = candidate_feature_vector(placeholder, size)
    novelty = novelty_score(feature, hall_features, args.novelty_neighbors)
    fitness = raw_score + novelty * args.novelty_weight

    candidate = copy.deepcopy(placeholder)
    candidate.novelty = novelty
    candidate.fitness = fitness

    return EvalOutcome(genome=genome, fitness=fitness, reject_code=None, candidate=candidate)


def build_solution_payload(
    level_number: int,
    candidate: Candidate,
    series_config: dict[str, object],
    evolution_config: dict[str, object],
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
            "mode": "v4_evolutionary",
            "seed": candidate.level_seed,
            "attempts_used": candidate.attempt_index,
            "series_config": series_config,
            "evolution": evolution_config,
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
            "raw_score": round(candidate.raw_score, 3),
            "novelty": round(candidate.novelty, 4),
            "score": round(candidate.fitness, 3),
            "genome": {
                "state_count": candidate.genome.state_count,
                "jump_bias": round(candidate.genome.jump_bias, 4),
                "rewire_ratio": round(candidate.genome.rewire_ratio, 4),
                "turn_left_bias": round(candidate.genome.turn_left_bias, 4),
                "open_scale": round(candidate.genome.open_scale, 4),
                "open_min_ratio": round(candidate.genome.open_min_ratio, 4),
                "open_max_ratio": round(candidate.genome.open_max_ratio, 4),
                "center_jitter": candidate.genome.center_jitter,
                "state_visit_cap": candidate.genome.state_visit_cap,
            },
        },
        "created_at": timestamp_now_utc(),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "V4 evolutionary level generator. Evolves program/board recipe genomes and keeps high-fitness "
            "candidates that satisfy strict solvability and anti-triviality constraints."
        )
    )
    parser.add_argument("level_number", type=int, help="Generate exactly this level number.")
    parser.add_argument("--out-dir", type=Path, default=Path("levels"), help="Public level output directory.")
    parser.add_argument("--solution-dir", type=Path, default=Path("solutions"), help="Private solution output directory.")

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

    parser.add_argument("--min-density", type=float, default=20.0, help="Minimum density driver percent.")
    parser.add_argument("--max-density", type=float, default=50.0, help="Maximum density driver percent.")
    parser.add_argument("--density-cycles", type=float, default=1.5, help="Density oscillation cycles.")
    parser.add_argument("--density-phase", type=float, default=1.2, help="Density phase in radians.")
    parser.add_argument("--density-noise", type=float, default=2.5, help="Density noise amplitude.")

    parser.add_argument("--center-jitter", type=int, default=8, help="Base center jitter for start position.")
    parser.add_argument("--state-visit-cap", type=int, default=40, help="Base guided state visit cap.")
    parser.add_argument("--generation-execution-limit", type=int, default=0, help="Generation execution cap (0=auto).")

    parser.add_argument("--min-instruction-coverage", type=float, default=0.86, help="Min executed instruction coverage.")
    parser.add_argument("--min-route-spread", type=float, default=0.08, help="Min route spread ratio.")
    parser.add_argument("--min-visited-size-factor", type=float, default=0.5, help="Min visited cells threshold = size*factor.")
    parser.add_argument("--min-steps-per-size", type=float, default=1.5, help="Min steps threshold = size*factor.")
    parser.add_argument("--min-solution-direction-types", type=int, default=3, help="Min direction types used by solution.")
    parser.add_argument("--max-straight-run", type=int, default=0, help="Reject straight run >= N (0 disables).")
    parser.add_argument("--min-direction-types-to-exit", type=int, default=2, help="Min direction types needed to exit.")

    parser.add_argument("--best-of", type=int, default=3, help="Valid candidates required per level.")
    parser.add_argument("--candidate-attempts", type=int, default=0, help="Hard cap on evaluations per level (0=none).")

    parser.add_argument("--population-size", type=int, default=48, help="Population size per generation.")
    parser.add_argument("--generations", type=int, default=28, help="Max generations per level.")
    parser.add_argument("--min-generations", type=int, default=6, help="Min generations before early stop.")
    parser.add_argument("--elite-count", type=int, default=8, help="Elite genomes copied to next generation.")
    parser.add_argument("--tournament-size", type=int, default=4, help="Tournament selection size.")
    parser.add_argument("--mutation-rate", type=float, default=0.55, help="Mutation chance for each child.")
    parser.add_argument("--crossover-rate", type=float, default=0.75, help="Crossover chance for each child.")
    parser.add_argument("--novelty-weight", type=float, default=220.0, help="Novelty contribution to fitness.")
    parser.add_argument("--novelty-neighbors", type=int, default=6, help="Number of neighbor distances for novelty.")

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
        help="Number of texture cleanup flip attempts (0 = auto from size and scale).",
    )
    parser.add_argument(
        "--texture-cleanup-scale",
        type=float,
        default=0.05,
        help="Auto cleanup attempts scale: round(width*height*scale) when attempts is 0.",
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
    if args.min_size < 2 or args.max_size < 2 or args.max_size < args.min_size:
        return False, "--min-size/--max-size must be >=2 and max>=min"
    if args.min_program_length < 20 or args.max_program_length < args.min_program_length:
        return False, "program length bounds are invalid"
    if args.max_program_length > core.MAX_PROGRAM_LIMIT:
        return False, f"--max-program-length must be <= {core.MAX_PROGRAM_LIMIT}"
    if args.program_slack < 0:
        return False, "--program-slack must be >= 0"
    if args.min_density < 0.0 or args.max_density > 100.0 or args.max_density < args.min_density:
        return False, "density bounds must satisfy 0 <= min <= max <= 100"
    if args.min_instruction_coverage <= 0.0 or args.min_instruction_coverage > 1.0:
        return False, "--min-instruction-coverage must be in (0,1]"
    if args.min_route_spread <= 0.0 or args.min_route_spread > 1.0:
        return False, "--min-route-spread must be in (0,1]"
    if args.min_visited_size_factor < 0.0 or args.min_steps_per_size < 0.0:
        return False, "size factors must be >= 0"
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
    if args.population_size < 4:
        return False, "--population-size must be >= 4"
    if args.generations < 1:
        return False, "--generations must be >= 1"
    if args.min_generations < 1 or args.min_generations > args.generations:
        return False, "--min-generations must be in [1, generations]"
    if args.elite_count < 1 or args.elite_count >= args.population_size:
        return False, "--elite-count must be >=1 and < population-size"
    if args.tournament_size < 2:
        return False, "--tournament-size must be >= 2"
    if args.mutation_rate < 0.0 or args.mutation_rate > 1.0:
        return False, "--mutation-rate must be in [0,1]"
    if args.crossover_rate < 0.0 or args.crossover_rate > 1.0:
        return False, "--crossover-rate must be in [0,1]"
    if args.novelty_neighbors < 1:
        return False, "--novelty-neighbors must be >= 1"
    if args.candidate_attempts < 0:
        return False, "--candidate-attempts must be >= 0"
    if args.texture_cleanup_attempts < 0:
        return False, "--texture-cleanup-attempts must be >= 0"
    if args.texture_cleanup_scale < 0.0:
        return False, "--texture-cleanup-scale must be >= 0"
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
        f"Generating V4-evo levels {args.start_level}..{args.max_level} "
        f"(seed={args.seed}, out={args.out_dir}, solutions={args.solution_dir}, "
        f"size={'fixed '+str(args.size) if args.size is not None else str(args.min_size)+'->'+str(args.max_size)}, "
        f"program_len={args.min_program_length}..{args.max_program_length}, "
        f"density_driver={args.min_density:.1f}%..{args.max_density:.1f}%, "
        f"best_of={args.best_of}, pop={args.population_size}, gens={args.generations}, "
        f"candidate_attempts={attempts_text}, seal_unreachable={'on' if args.seal_unreachable else 'off'}, "
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

    evolution_config = {
        "population_size": args.population_size,
        "generations": args.generations,
        "min_generations": args.min_generations,
        "elite_count": args.elite_count,
        "tournament_size": args.tournament_size,
        "mutation_rate": args.mutation_rate,
        "crossover_rate": args.crossover_rate,
        "novelty_weight": args.novelty_weight,
        "novelty_neighbors": args.novelty_neighbors,
    }

    for level_number in range(args.start_level, args.max_level + 1):
        size = choose_size(level_number, args)
        target_program_length = choose_target_program_length(level_number, args, batch_rng)
        density_driver_percent = choose_density_percent(level_number, args, batch_rng)
        min_steps_required = max(40, int(round(size * args.min_steps_per_size)))
        min_visited_required = max(12, int(round(size * args.min_visited_size_factor)))

        progress_width = 0
        evaluations = 0
        reject_counts: dict[str, int] = {}

        hall_by_hash: dict[str, Candidate] = {}
        scored_population: list[EvalOutcome] = []

        population = [
            random_genome(size, target_program_length, args, batch_rng)
            for _ in range(args.population_size)
        ]

        if args.verbose:
            print(
                f"Level {level_number} constraints: size={size}x{size}, target_program_len={target_program_length}, "
                f"density_driver={density_driver_percent:.1f}%, min_steps={min_steps_required}, "
                f"min_visited={min_visited_required}, min_spread={args.min_route_spread:.2f}, "
                f"min_solution_dir_types={args.min_solution_direction_types}, "
                f"min_exit_dir_types={args.min_direction_types_to_exit}, best_of={args.best_of}, "
                f"pop={args.population_size}, gens={args.generations}, eval_cap={attempts_text}, "
                f"texture_cleanup={'on' if args.texture_cleanup else 'off'}"
            )

        best_fitness = -1e9
        completed_generations = 0

        for generation_index in range(args.generations):
            generation_outcomes: list[EvalOutcome] = []
            hall_features = [candidate_feature_vector(candidate, size) for candidate in hall_by_hash.values()]

            for genome in population:
                if args.candidate_attempts > 0 and evaluations >= args.candidate_attempts:
                    break

                evaluations += 1
                level_seed = batch_rng.randrange(0, 2**63)
                outcome = evaluate_genome(
                    v3=v3,
                    genome=genome,
                    level_number=level_number,
                    size=size,
                    target_program_length=target_program_length,
                    density_driver_percent=density_driver_percent,
                    min_steps_required=min_steps_required,
                    min_visited_required=min_visited_required,
                    args=args,
                    level_seed=level_seed,
                    attempt_index=evaluations,
                    hall_features=hall_features,
                )

                generation_outcomes.append(outcome)
                if outcome.candidate is None:
                    code = outcome.reject_code or "??"
                    reject_counts[code] = reject_counts.get(code, 0) + 1
                    status = f"rejected({code})"
                else:
                    candidate = outcome.candidate
                    existing = hall_by_hash.get(candidate.level_hash)
                    if existing is None or candidate.fitness > existing.fitness:
                        hall_by_hash[candidate.level_hash] = candidate
                    best_fitness = max(best_fitness, candidate.fitness)
                    status = "candidate_ok"

                if show_live_progress:
                    attempts_value = f"{evaluations:>{attempts_width}d}"
                    progress_width = update_progress_line(
                        f"Level {level_number}/{args.max_level}: gen={generation_index + 1}/{args.generations}, "
                        f"evals={attempts_value}/{attempts_text}, best_of={min(len(hall_by_hash), args.best_of)}/{args.best_of}, "
                        f"best_fit={best_fitness:.1f}, status={status}, {format_reject_counts(reject_counts)}",
                        progress_width,
                        show_live_progress,
                    )

            if not generation_outcomes:
                break

            generation_outcomes.sort(key=lambda item: item.fitness, reverse=True)
            scored_population = generation_outcomes
            completed_generations = generation_index + 1

            if len(hall_by_hash) >= args.best_of and completed_generations >= args.min_generations:
                break
            if args.candidate_attempts > 0 and evaluations >= args.candidate_attempts:
                break

            elites = [outcome.genome for outcome in generation_outcomes[: args.elite_count]]
            next_population: list[Genome] = [copy.deepcopy(genome) for genome in elites]

            while len(next_population) < args.population_size:
                if batch_rng.random() < args.crossover_rate:
                    parent_a = tournament_select(generation_outcomes, batch_rng, args.tournament_size)
                    parent_b = tournament_select(generation_outcomes, batch_rng, args.tournament_size)
                    child = crossover_genomes(parent_a, parent_b, batch_rng)
                else:
                    parent = tournament_select(generation_outcomes, batch_rng, args.tournament_size)
                    child = copy.deepcopy(parent)

                if batch_rng.random() < args.mutation_rate:
                    child = mutate_genome(child, size, args, batch_rng)

                child = clamp_genome(child, size, args)
                next_population.append(child)

            population = next_population

        clear_progress_line(progress_width, show_live_progress)

        hall_candidates = sorted(hall_by_hash.values(), key=lambda candidate: candidate.fitness, reverse=True)
        if len(hall_candidates) < args.best_of:
            print(
                f"Error generating level {level_number}: found {len(hall_candidates)}/{args.best_of} valid candidates "
                f"after {evaluations} evaluations and {completed_generations} generations "
                f"(rejects: {format_reject_counts(reject_counts)}).",
                file=sys.stderr,
            )
            return 2

        chosen = hall_candidates[0]
        solution_payload = build_solution_payload(
            level_number=level_number,
            candidate=chosen,
            series_config=series_config,
            evolution_config=evolution_config,
        )

        level_path = args.out_dir / f"{level_number}.level"
        solution_path = args.solution_dir / f"{level_number}.solution.json"
        level_path.write_text(chosen.level_text + "\n", encoding="utf-8")
        solution_path.write_text(json.dumps(solution_payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")

        print(
            f"Level {level_number}: ok "
            f"(size={chosen.level.width}x{chosen.level.height}, plim={chosen.level.program_limit}, "
            f"elim={chosen.level.execution_limit}, evals={evaluations}, gens={completed_generations}, "
            f"best_of={min(len(hall_candidates), args.best_of)}/{args.best_of}, "
            f"solution_steps={chosen.solution_steps}, visited={chosen.visited_cell_count}, "
            f"spread={chosen.spread_ratio:.3f}, direction_types_used={chosen.direction_types_used}, "
            f"density_driver={chosen.density_driver_percent:.1f}%, density_target={chosen.target_density_percent:.1f}%, "
            f"density_final={chosen.final_density_percent:.1f}%, cb2x2={chosen.checkerboard_2x2}, "
            f"texture_flips={chosen.texture_flips_applied}/{chosen.texture_cleanup_attempts}, "
            f"min_moves_to_exit={chosen.min_moves_to_exit}, "
            f"min_direction_types_to_exit={chosen.min_direction_types_to_exit}, "
            f"score={chosen.fitness:.1f}, novelty={chosen.novelty:.3f})"
        )
        if args.verbose:
            print(f"Level {level_number} solution_program: {core.format_program(chosen.solution)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
