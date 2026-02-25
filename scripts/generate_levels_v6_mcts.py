#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import random
import sys
from dataclasses import dataclass, field
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
class SearchSpace:
    names: list[str]
    options: list[list[float | int]]


@dataclass
class MctsNode:
    depth: int
    parent: MctsNode | None
    action_from_parent: int | None
    untried_actions: list[int]
    visits: int = 0
    value_sum: float = 0.0
    children: dict[int, "MctsNode"] = field(default_factory=dict)



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



def load_module(script_name: str, module_name: str):
    module_path = ROOT_DIR / "scripts" / script_name
    if not module_path.exists():
        raise FileNotFoundError(f"Required file not found: {module_path}")
    spec = importlib.util.spec_from_file_location(module_name, str(module_path))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load {script_name}")
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



def dedupe_sorted(values: list[float | int]) -> list[float | int]:
    out: list[float | int] = []
    for value in sorted(values):
        if out and out[-1] == value:
            continue
        out.append(value)
    return out



def build_search_space(v4, size: int, target_program_length: int, args: argparse.Namespace) -> SearchSpace:
    state_options = v4.ordered_state_counts(args.min_program_length, args.max_program_length)
    min_state = min(state_options)
    max_state = max(state_options)
    target_states = clamp_int(int(round(target_program_length / 4.0)), min_state, max_state)
    state_choices = [
        clamp_int(target_states - 3, min_state, max_state),
        clamp_int(target_states - 2, min_state, max_state),
        clamp_int(target_states - 1, min_state, max_state),
        target_states,
        clamp_int(target_states + 1, min_state, max_state),
        clamp_int(target_states + 2, min_state, max_state),
        clamp_int(target_states + 3, min_state, max_state),
    ]
    state_choices = [int(value) for value in dedupe_sorted(state_choices)]

    jitter_mid = max(1, args.center_jitter // 2)
    jitter_hi = max(jitter_mid + 1, args.center_jitter)
    center_jitter_choices = [0, jitter_mid, jitter_hi]

    visit_low = max(8, args.state_visit_cap // 2)
    visit_mid = max(visit_low + 1, args.state_visit_cap)
    visit_high = max(visit_mid + 1, int(round(args.state_visit_cap * 1.7)))
    state_visit_choices = [visit_low, visit_mid, visit_high]

    open_min_mid = clamp_float(
        args.min_reachable_open_ratio + 0.04,
        args.min_reachable_open_ratio,
        min(0.55, args.max_reachable_open_ratio - 0.02),
    )
    open_min_hi = clamp_float(
        args.min_reachable_open_ratio + 0.08,
        args.min_reachable_open_ratio,
        min(0.55, args.max_reachable_open_ratio - 0.02),
    )
    open_max_low = clamp_float(
        args.max_reachable_open_ratio - 0.08,
        max(args.min_reachable_open_ratio + 0.02, 0.12),
        args.max_reachable_open_ratio,
    )
    open_max_mid = clamp_float(
        args.max_reachable_open_ratio - 0.04,
        max(args.min_reachable_open_ratio + 0.02, 0.16),
        args.max_reachable_open_ratio,
    )

    names = [
        "state_count",
        "jump_bias",
        "rewire_ratio",
        "turn_left_bias",
        "open_scale",
        "open_min_ratio",
        "open_max_ratio",
        "center_jitter",
        "state_visit_cap",
    ]
    options: list[list[float | int]] = [
        state_choices,
        [0.12, 0.26, 0.40, 0.55, 0.70, 0.84],
        [0.0, 0.08, 0.16, 0.24, 0.32, 0.40],
        [0.18, 0.33, 0.50, 0.67, 0.82],
        [0.35, 0.50, 0.65, 0.80, 0.95, 1.10],
        [args.min_reachable_open_ratio, open_min_mid, open_min_hi],
        [open_max_low, open_max_mid, args.max_reachable_open_ratio],
        [int(value) for value in dedupe_sorted(center_jitter_choices)],
        [int(value) for value in dedupe_sorted(state_visit_choices)],
    ]

    options[5] = [float(value) for value in dedupe_sorted(options[5])]
    options[6] = [float(value) for value in dedupe_sorted(options[6])]

    return SearchSpace(names=names, options=options)



def genome_from_indices(v4, indices: list[int], space: SearchSpace, size: int, target_program_length: int, args: argparse.Namespace) -> object:
    # Start from a randomized baseline to keep stochastic micro-variation.
    baseline_rng = random.Random(0x9E3779B97F4A7C15 + sum((i + 1) * v for i, v in enumerate(indices)))
    genome = v4.random_genome(size, target_program_length, args, baseline_rng)

    chosen = [space.options[d][indices[d]] for d in range(len(space.options))]
    genome.state_count = int(chosen[0])
    genome.jump_bias = float(chosen[1])
    genome.rewire_ratio = float(chosen[2])
    genome.turn_left_bias = float(chosen[3])
    genome.open_scale = float(chosen[4])
    genome.open_min_ratio = float(chosen[5])
    genome.open_max_ratio = float(chosen[6])
    genome.center_jitter = int(chosen[7])
    genome.state_visit_cap = int(chosen[8])

    if genome.open_max_ratio <= genome.open_min_ratio + 0.02:
        genome.open_max_ratio = min(0.60, genome.open_min_ratio + 0.02)
    return v4.clamp_genome(genome, size, args)



def create_node(depth: int, parent: MctsNode | None, action: int | None, branching: int, rng: random.Random) -> MctsNode:
    actions = list(range(branching))
    rng.shuffle(actions)
    return MctsNode(
        depth=depth,
        parent=parent,
        action_from_parent=action,
        untried_actions=actions,
        visits=0,
        value_sum=0.0,
    )



def select_child_uct(node: MctsNode, exploration_c: float) -> tuple[int, MctsNode]:
    if not node.children:
        raise RuntimeError("Cannot select from empty child set")
    log_parent = math.log(max(1, node.visits))
    best_action = -1
    best_child: MctsNode | None = None
    best_score = -1e18

    for action, child in node.children.items():
        if child.visits <= 0:
            uct = 1e9
        else:
            mean_value = child.value_sum / float(child.visits)
            bonus = exploration_c * math.sqrt(log_parent / float(child.visits))
            uct = mean_value + bonus
        if uct > best_score:
            best_score = uct
            best_action = action
            best_child = child

    if best_child is None:
        raise RuntimeError("UCT child selection failed")
    return best_action, best_child



def selection_and_expansion(
    root: MctsNode,
    space: SearchSpace,
    exploration_c: float,
    rng: random.Random,
) -> tuple[list[MctsNode], list[int]]:
    node = root
    path = [node]
    choices: list[int] = []
    max_depth = len(space.options)

    while node.depth < max_depth:
        if node.untried_actions:
            action = node.untried_actions.pop()
            next_depth = node.depth + 1
            branching = len(space.options[next_depth]) if next_depth < max_depth else 0
            child = create_node(next_depth, node, action, branching, rng)
            node.children[action] = child
            choices.append(action)
            path.append(child)
            node = child
            break

        action, child = select_child_uct(node, exploration_c)
        choices.append(action)
        node = child
        path.append(node)

    while len(choices) < max_depth:
        dim = len(choices)
        action = rng.randrange(0, len(space.options[dim]))
        choices.append(action)

    return path, choices



def backpropagate(path: list[MctsNode], value: float) -> None:
    for node in path:
        node.visits += 1
        node.value_sum += value



def mcts_value_from_outcome(outcome, args: argparse.Namespace) -> float:
    if outcome.candidate is None:
        return args.mcts_reject_value
    return math.tanh(outcome.fitness / args.mcts_value_scale)



def count_tree_nodes(root: MctsNode) -> int:
    total = 1
    stack = list(root.children.values())
    while stack:
        node = stack.pop()
        total += 1
        stack.extend(node.children.values())
    return total



def build_solution_payload(
    level_number: int,
    candidate,
    series_config: dict[str, object],
    mcts_config: dict[str, object],
    tree_nodes: int,
    completed_iterations: int,
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
            "mode": "v6_mcts",
            "seed": candidate.level_seed,
            "attempts_used": candidate.attempt_index,
            "series_config": series_config,
            "mcts": {
                **mcts_config,
                "tree_nodes": tree_nodes,
                "iterations": completed_iterations,
            },
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
            "V6 MCTS level generator. Uses Monte Carlo Tree Search over generator-recipe choices "
            "and evaluates candidates with the v4 constraint pipeline."
        )
    )
    parser.add_argument("max_level", type=int, help="Generate up to this level number.")
    parser.add_argument("--start-level", type=int, default=1, help="Starting level number (default: 1).")
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

    parser.add_argument(
        "--min-reachable-open-ratio",
        type=float,
        default=0.10,
        help="Lower bound used by MCTS recipe search for connected open ratio.",
    )
    parser.add_argument(
        "--max-reachable-open-ratio",
        type=float,
        default=0.42,
        help="Upper bound used by MCTS recipe search for connected open ratio.",
    )

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
    parser.add_argument(
        "--candidate-attempts",
        type=int,
        default=0,
        help="Hard cap on evaluations per level (0=unbounded; mcts-iterations still bounds).",
    )

    parser.add_argument("--mcts-iterations", type=int, default=900, help="Maximum MCTS simulations per level.")
    parser.add_argument("--mcts-min-iterations", type=int, default=220, help="Minimum simulations before early stop.")
    parser.add_argument("--mcts-exploration", type=float, default=1.35, help="UCT exploration constant.")
    parser.add_argument("--mcts-value-scale", type=float, default=3000.0, help="Scale for tanh normalization of accepted fitness.")
    parser.add_argument("--mcts-reject-value", type=float, default=-1.0, help="Backprop value used for rejected candidates.")

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
    if args.min_reachable_open_ratio <= 0.0 or args.min_reachable_open_ratio > 0.6:
        return False, "--min-reachable-open-ratio must be in (0,0.6]"
    if args.max_reachable_open_ratio <= 0.0 or args.max_reachable_open_ratio > 0.7:
        return False, "--max-reachable-open-ratio must be in (0,0.7]"
    if args.max_reachable_open_ratio < args.min_reachable_open_ratio + 0.02:
        return False, "--max-reachable-open-ratio must be at least min+0.02"
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
    if args.candidate_attempts < 0:
        return False, "--candidate-attempts must be >= 0"
    if args.mcts_iterations < 1:
        return False, "--mcts-iterations must be >= 1"
    if args.mcts_min_iterations < 1 or args.mcts_min_iterations > args.mcts_iterations:
        return False, "--mcts-min-iterations must be in [1, mcts-iterations]"
    if args.mcts_exploration <= 0.0:
        return False, "--mcts-exploration must be > 0"
    if args.mcts_value_scale <= 0.0:
        return False, "--mcts-value-scale must be > 0"
    if args.novelty_neighbors < 1:
        return False, "--novelty-neighbors must be >= 1"
    if args.texture_cleanup_attempts < 0:
        return False, "--texture-cleanup-attempts must be >= 0"
    if args.texture_cleanup_scale < 0.0:
        return False, "--texture-cleanup-scale must be >= 0"
    return True, ""



def main(argv: list[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

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
        v3 = load_module("generate_levels_v3.py", "_robotsrevenge_v3")
        v4 = load_module("generate_levels_v4_evo.py", "_robotsrevenge_v4")
    except Exception as exc:  # noqa: BLE001
        print(f"Error: could not load generator helpers: {exc}", file=sys.stderr)
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
        f"Generating V6-MCTS levels {args.start_level}..{args.max_level} "
        f"(seed={args.seed}, out={args.out_dir}, solutions={args.solution_dir}, "
        f"size={'fixed '+str(args.size) if args.size is not None else str(args.min_size)+'->'+str(args.max_size)}, "
        f"program_len={args.min_program_length}..{args.max_program_length}, "
        f"density_driver={args.min_density:.1f}%..{args.max_density:.1f}%, "
        f"best_of={args.best_of}, mcts_iterations={args.mcts_iterations}, "
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

    mcts_config = {
        "iterations": args.mcts_iterations,
        "min_iterations": args.mcts_min_iterations,
        "exploration": args.mcts_exploration,
        "value_scale": args.mcts_value_scale,
        "reject_value": args.mcts_reject_value,
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
        hall_by_hash: dict[str, object] = {}

        space = build_search_space(v4, size, target_program_length, args)
        root = create_node(0, None, None, len(space.options[0]), batch_rng)

        if args.verbose:
            print(
                f"Level {level_number} constraints: size={size}x{size}, target_program_len={target_program_length}, "
                f"density_driver={density_driver_percent:.1f}%, min_steps={min_steps_required}, "
                f"min_visited={min_visited_required}, min_spread={args.min_route_spread:.2f}, "
                f"min_solution_dir_types={args.min_solution_direction_types}, "
                f"min_exit_dir_types={args.min_direction_types_to_exit}, best_of={args.best_of}, "
                f"mcts_iterations={args.mcts_iterations}, eval_cap={attempts_text}, "
                f"texture_cleanup={'on' if args.texture_cleanup else 'off'}"
            )

        best_fitness = -1e9
        completed_iterations = 0

        while completed_iterations < args.mcts_iterations:
            if args.candidate_attempts > 0 and evaluations >= args.candidate_attempts:
                break

            completed_iterations += 1
            path, choice_indices = selection_and_expansion(
                root=root,
                space=space,
                exploration_c=args.mcts_exploration,
                rng=batch_rng,
            )

            genome = genome_from_indices(
                v4=v4,
                indices=choice_indices,
                space=space,
                size=size,
                target_program_length=target_program_length,
                args=args,
            )

            evaluations += 1
            level_seed = batch_rng.randrange(0, 2**63)
            hall_features = [v4.candidate_feature_vector(candidate, size) for candidate in hall_by_hash.values()]
            outcome = v4.evaluate_genome(
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

            backpropagate(path, mcts_value_from_outcome(outcome, args))

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
                    f"Level {level_number}/{args.max_level}: sim={completed_iterations}/{args.mcts_iterations}, "
                    f"evals={attempts_value}/{attempts_text}, best_of={min(len(hall_by_hash), args.best_of)}/{args.best_of}, "
                    f"best_fit={best_fitness:.1f}, status={status}, {format_reject_counts(reject_counts)}",
                    progress_width,
                    show_live_progress,
                )

            if len(hall_by_hash) >= args.best_of and completed_iterations >= args.mcts_min_iterations:
                break

        clear_progress_line(progress_width, show_live_progress)

        hall_candidates = sorted(hall_by_hash.values(), key=lambda candidate: candidate.fitness, reverse=True)
        if len(hall_candidates) < args.best_of:
            print(
                f"Error generating level {level_number}: found {len(hall_candidates)}/{args.best_of} valid candidates "
                f"after {evaluations} evaluations and {completed_iterations} simulations "
                f"(rejects: {format_reject_counts(reject_counts)}).",
                file=sys.stderr,
            )
            return 2

        chosen = hall_candidates[0]
        tree_nodes = count_tree_nodes(root)
        solution_payload = build_solution_payload(
            level_number=level_number,
            candidate=chosen,
            series_config=series_config,
            mcts_config=mcts_config,
            tree_nodes=tree_nodes,
            completed_iterations=completed_iterations,
        )

        level_path = args.out_dir / f"{level_number}.level"
        solution_path = args.solution_dir / f"{level_number}.solution.json"
        level_path.write_text(chosen.level_text + "\n", encoding="utf-8")
        solution_path.write_text(json.dumps(solution_payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")

        print(
            f"Level {level_number}: ok "
            f"(size={chosen.level.width}x{chosen.level.height}, plim={chosen.level.program_limit}, "
            f"elim={chosen.level.execution_limit}, evals={evaluations}, sims={completed_iterations}, "
            f"tree_nodes={tree_nodes}, best_of={min(len(hall_candidates), args.best_of)}/{args.best_of}, "
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
