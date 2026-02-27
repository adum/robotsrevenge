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
    "mg",  # mission graph synthesis failed
    "mc",  # mission compiler produced invalid recipe
    "ce",  # short counterexample program found
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
class MissionNode:
    index: int
    x: int
    y: int


@dataclass
class MissionEdge:
    src: int
    dst: int
    kind: str  # main | loop | decoy


@dataclass
class MissionGraph:
    nodes: list[MissionNode]
    edges: list[MissionEdge]
    main_edges: int
    loop_edges: int
    decoy_edges: int
    cycle_depth_est: int
    spread_ratio: float
    branching_ratio: float


@dataclass
class CandidateRecord:
    candidate: object
    level_seed: int
    attempt_index: int
    mission: MissionGraph
    mission_recipe: dict[str, float | int]
    mission_bonus: float
    combined_score: float
    cegis_evaluations: int


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


def mission_spread_ratio(nodes: list[MissionNode], size: int) -> float:
    if not nodes or size <= 0:
        return 0.0
    min_x = min(node.x for node in nodes)
    max_x = max(node.x for node in nodes)
    min_y = min(node.y for node in nodes)
    max_y = max(node.y for node in nodes)
    area = (max_x - min_x + 1) * (max_y - min_y + 1)
    return area / float(size * size)


def mission_branching_ratio(edges: list[MissionEdge], node_count: int) -> float:
    if node_count <= 0:
        return 0.0
    outgoing = [0 for _ in range(node_count)]
    for edge in edges:
        if 0 <= edge.src < node_count:
            outgoing[edge.src] += 1
    branching_nodes = sum(1 for degree in outgoing if degree >= 2)
    return branching_nodes / float(node_count)


def synthesize_mission_graph(size: int, args: argparse.Namespace, rng: random.Random) -> MissionGraph | None:
    if size < 3:
        return None

    node_count = rng.randint(args.mission_nodes_min, args.mission_nodes_max)
    node_count = max(3, node_count)

    min_coord = 1
    max_coord = size - 2
    if min_coord > max_coord:
        min_coord = 0
        max_coord = size - 1

    nodes: list[MissionNode] = []
    center = size // 2
    nodes.append(MissionNode(index=0, x=clamp_int(center, min_coord, max_coord), y=clamp_int(center, min_coord, max_coord)))

    min_dist = max(1, size // 10)
    for index in range(1, node_count):
        placed = False
        for _ in range(120):
            x = rng.randint(min_coord, max_coord)
            y = rng.randint(min_coord, max_coord)
            if all(abs(x - node.x) + abs(y - node.y) >= min_dist for node in nodes):
                nodes.append(MissionNode(index=index, x=x, y=y))
                placed = True
                break
        if not placed:
            # Fallback keeps generation deterministic but may reduce mission richness.
            nodes.append(MissionNode(index=index, x=rng.randint(min_coord, max_coord), y=rng.randint(min_coord, max_coord)))

    edges: list[MissionEdge] = []
    edge_set: set[tuple[int, int]] = set()

    # Main path is always a chain through mission nodes.
    for index in range(node_count - 1):
        src = index
        dst = index + 1
        edges.append(MissionEdge(src=src, dst=dst, kind="main"))
        edge_set.add((src, dst))

    loop_target = clamp_int(args.loop_depth_target + rng.randint(-1, 1), 0, max(0, node_count - 2))
    loop_edges = 0
    max_loop_depth_seen = 0
    for _ in range(loop_target * 3 + 6):
        if loop_edges >= loop_target:
            break
        src = rng.randint(1, node_count - 1)
        dst = rng.randint(0, src - 1)
        if src == dst or (src, dst) in edge_set:
            continue
        edges.append(MissionEdge(src=src, dst=dst, kind="loop"))
        edge_set.add((src, dst))
        loop_edges += 1
        max_loop_depth_seen = max(max_loop_depth_seen, src - dst)

    decoy_target = clamp_int(
        args.decoy_depth_target + rng.randint(0, 2),
        0,
        max(0, node_count * 2),
    )
    decoy_edges = 0
    for _ in range(decoy_target * 4 + 8):
        if decoy_edges >= decoy_target:
            break
        src = rng.randint(0, node_count - 2)
        dst = rng.randint(src + 1, node_count - 1)
        if src == dst or (src, dst) in edge_set:
            continue
        edges.append(MissionEdge(src=src, dst=dst, kind="decoy"))
        edge_set.add((src, dst))
        decoy_edges += 1

    spread_ratio = mission_spread_ratio(nodes, size)
    branching_ratio = mission_branching_ratio(edges, node_count)
    cycle_depth_est = max(1, max_loop_depth_seen if loop_edges > 0 else 1)

    if spread_ratio < args.mission_spread_target * 0.35:
        return None

    return MissionGraph(
        nodes=nodes,
        edges=edges,
        main_edges=node_count - 1,
        loop_edges=loop_edges,
        decoy_edges=decoy_edges,
        cycle_depth_est=cycle_depth_est,
        spread_ratio=spread_ratio,
        branching_ratio=branching_ratio,
    )


def compile_mission_to_genome(
    v4,
    mission: MissionGraph,
    size: int,
    target_program_length: int,
    density_driver_percent: float,
    args: argparse.Namespace,
    rng: random.Random,
):
    state_options = v4.ordered_state_counts(args.min_program_length, args.max_program_length)
    min_state = min(state_options)
    max_state = max(state_options)

    # Start from the v4 random prior (known to produce viable neighborhoods),
    # then steer toward mission-derived parameters.
    seed_genome = v4.random_genome(size, target_program_length, args, rng)

    target_states = clamp_int(int(round(target_program_length / 4.0)), min_state, max_state)
    soft_max_state = clamp_int(max(6, target_states + 1), min_state, max_state)
    complexity = (
        mission.loop_edges * 0.9
        + mission.decoy_edges * 0.45
        + mission.branching_ratio * 4.0
        + mission.cycle_depth_est * 0.4
    )
    mission_state_count = clamp_int(
        int(round(target_states + complexity + rng.uniform(-1.2, 1.2))),
        min_state,
        soft_max_state,
    )
    state_count = clamp_int(
        int(round(seed_genome.state_count * 0.55 + mission_state_count * 0.45)),
        min_state,
        soft_max_state,
    )

    mission_jump_bias = 0.20 + mission.loop_edges * 0.07 + args.ambiguity_target * 0.35 + rng.uniform(-0.06, 0.06)
    jump_bias = clamp_float(
        seed_genome.jump_bias * 0.50 + mission_jump_bias * 0.50,
        0.05,
        0.95,
    )
    mission_rewire = 0.04 + mission.decoy_edges * 0.03 + mission.branching_ratio * 0.20 + rng.uniform(-0.05, 0.05)
    rewire_ratio = clamp_float(
        seed_genome.rewire_ratio * 0.50 + mission_rewire * 0.50,
        0.0,
        0.6,
    )
    turn_left_bias = clamp_float(seed_genome.turn_left_bias * 0.5 + (0.5 + rng.uniform(-0.28, 0.28)) * 0.5, 0.05, 0.95)

    base_open = 1.0 - density_driver_percent / 100.0
    mission_open_scale = (0.45 + args.ambiguity_target * 0.85) * (0.84 + mission.decoy_edges * 0.02)
    open_scale = clamp_float(
        seed_genome.open_scale * 0.45 + mission_open_scale * 0.55,
        0.2,
        1.2,
    )
    open_mid = clamp_float(base_open * open_scale, args.min_reachable_open_ratio, args.max_reachable_open_ratio)
    open_window = clamp_float(0.03 + mission.branching_ratio * 0.08, 0.02, 0.20)
    open_min_ratio = clamp_float(open_mid - open_window, 0.05, 0.58)
    open_max_ratio = clamp_float(max(open_min_ratio + 0.02, open_mid + open_window), open_min_ratio + 0.02, 0.60)

    center_jitter_cap = max(2, size // 6)
    center_jitter = clamp_int(
        int(round(seed_genome.center_jitter * 0.5 + args.center_jitter * (0.35 + mission.spread_ratio) * 0.5)),
        0,
        center_jitter_cap,
    )
    state_visit_cap = clamp_int(
        int(
            round(
                seed_genome.state_visit_cap * 0.45
                + (args.state_visit_cap + mission.loop_edges * 3 + mission.branching_ratio * 18) * 0.55
            )
        ),
        8,
        160,
    )

    genome = v4.Genome(
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
    genome = v4.clamp_genome(genome, size, args)

    recipe = {
        "state_count": genome.state_count,
        "jump_bias": round(genome.jump_bias, 4),
        "rewire_ratio": round(genome.rewire_ratio, 4),
        "turn_left_bias": round(genome.turn_left_bias, 4),
        "open_scale": round(genome.open_scale, 4),
        "open_min_ratio": round(genome.open_min_ratio, 4),
        "open_max_ratio": round(genome.open_max_ratio, 4),
        "center_jitter": genome.center_jitter,
        "state_visit_cap": genome.state_visit_cap,
    }
    return genome, recipe


def weighted_choice(
    options: list[tuple[tuple[int, int], float]],
    rng: random.Random,
) -> tuple[int, int]:
    if not options:
        raise RuntimeError("weighted_choice called with empty options")
    total = sum(max(0.0, weight) for _, weight in options)
    if total <= 0.0:
        return options[rng.randrange(0, len(options))][0]
    pick = rng.random() * total
    acc = 0.0
    for cell, weight in options:
        w = max(0.0, weight)
        acc += w
        if pick <= acc:
            return cell
    return options[-1][0]


def carve_disk(board: list[list[bool]], x: int, y: int, radius: int) -> None:
    height = len(board)
    width = len(board[0]) if height else 0
    if width <= 0 or height <= 0:
        return
    r = max(0, radius)
    for ny in range(max(0, y - r), min(height, y + r + 1)):
        for nx in range(max(0, x - r), min(width, x + r + 1)):
            if abs(nx - x) + abs(ny - y) <= r + (1 if r >= 2 else 0):
                board[ny][nx] = False


def carve_noisy_corridor(
    board: list[list[bool]],
    x0: int,
    y0: int,
    x1: int,
    y1: int,
    base_radius: int,
    wander: float,
    rng: random.Random,
) -> None:
    width = len(board[0]) if board else 0
    height = len(board)
    if width <= 0 or height <= 0:
        return

    x = clamp_int(x0, 0, width - 1)
    y = clamp_int(y0, 0, height - 1)
    tx = clamp_int(x1, 0, width - 1)
    ty = clamp_int(y1, 0, height - 1)

    max_steps = max(8, (abs(tx - x) + abs(ty - y)) * 6)
    for _ in range(max_steps):
        carve_disk(board, x, y, base_radius + (1 if rng.random() < 0.08 else 0))
        if x == tx and y == ty:
            break

        current_dist = abs(x - tx) + abs(y - ty)
        options: list[tuple[tuple[int, int], float]] = []
        for dx, dy in core.DIR_DELTAS:
            nx = x + dx
            ny = y + dy
            if not core.in_bounds(nx, ny, width, height):
                continue
            nd = abs(nx - tx) + abs(ny - ty)
            improve = current_dist - nd
            weight = 0.05 + max(0.0, improve) * 2.2
            if nd <= current_dist + 1:
                weight += 0.3
            if improve < 0:
                weight *= 0.25
            if rng.random() < wander:
                weight += 0.2
            options.append(((nx, ny), weight))

        if not options:
            break
        x, y = weighted_choice(options, rng)
    carve_disk(board, tx, ty, base_radius)


def open_neighbors_count(board: list[list[bool]], x: int, y: int) -> int:
    width = len(board[0]) if board else 0
    height = len(board)
    count = 0
    for dx, dy in core.DIR_DELTAS:
        nx = x + dx
        ny = y + dy
        if not core.in_bounds(nx, ny, width, height):
            continue
        if not board[ny][nx]:
            count += 1
    return count


def nearest_node_distance(x: int, y: int, nodes: list[MissionNode]) -> int:
    if not nodes:
        return 0
    return min(abs(x - node.x) + abs(y - node.y) for node in nodes)


def build_mission_board(
    size: int,
    mission: MissionGraph,
    target_open_ratio: float,
    requirements: dict[tuple[int, int], bool],
    start_x: int,
    start_y: int,
    rng: random.Random,
) -> list[list[bool]] | None:
    if size < 2:
        return None

    board = [[True for _ in range(size)] for _ in range(size)]
    node_out = [0 for _ in mission.nodes]
    node_in = [0 for _ in mission.nodes]
    for edge in mission.edges:
        if 0 <= edge.src < len(mission.nodes):
            node_out[edge.src] += 1
        if 0 <= edge.dst < len(mission.nodes):
            node_in[edge.dst] += 1

    # Carve local regions around mission nodes.
    for node in mission.nodes:
        branch_score = node_out[node.index] + node_in[node.index]
        radius = 1
        if branch_score >= 3 and rng.random() < 0.75:
            radius += 1
        if branch_score >= 5 and rng.random() < 0.35:
            radius += 1
        carve_disk(board, node.x, node.y, radius)

    # Carve mission edge corridors with style differences by edge kind.
    for edge in mission.edges:
        src = mission.nodes[edge.src]
        dst = mission.nodes[edge.dst]
        if edge.kind == "main":
            base_radius = 1 + (1 if rng.random() < 0.42 else 0)
            wander = 0.06
        elif edge.kind == "loop":
            base_radius = 1
            wander = 0.12
        else:  # decoy
            base_radius = 1
            wander = 0.22
        carve_noisy_corridor(
            board=board,
            x0=src.x,
            y0=src.y,
            x1=dst.x,
            y1=dst.y,
            base_radius=base_radius,
            wander=wander,
            rng=rng,
        )

        if edge.kind == "decoy" and rng.random() < 0.85:
            # Give decoys pocket-like geometry.
            mx = (src.x + dst.x) // 2
            my = (src.y + dst.y) // 2
            carve_disk(board, mx, my, 1 + (1 if rng.random() < 0.35 else 0))

    total_cells = size * size
    target_open_cells = clamp_int(int(round(total_cells * target_open_ratio)), 1, total_cells)

    required_open: set[tuple[int, int]] = set()
    required_blocked: set[tuple[int, int]] = set()
    for (x, y), blocked in requirements.items():
        if not core.in_bounds(x, y, size, size):
            continue
        if blocked:
            required_blocked.add((x, y))
        else:
            required_open.add((x, y))
    required_open.add((start_x, start_y))
    required_open -= required_blocked

    for x, y in required_open:
        board[y][x] = False
    for x, y in required_blocked:
        board[y][x] = True
    board[start_y][start_x] = False

    open_cells = {(x, y) for y in range(size) for x in range(size) if not board[y][x]}
    if len(required_open) > target_open_cells:
        return None

    # Expand openness to target while preserving mission texture.
    def frontier_cells() -> list[tuple[int, int]]:
        cells: list[tuple[int, int]] = []
        seen: set[tuple[int, int]] = set()
        for x, y in open_cells:
            for dx, dy in core.DIR_DELTAS:
                nx = x + dx
                ny = y + dy
                if not core.in_bounds(nx, ny, size, size):
                    continue
                cell = (nx, ny)
                if cell in seen or cell in open_cells or cell in required_blocked:
                    continue
                seen.add(cell)
                cells.append(cell)
        return cells

    while len(open_cells) < target_open_cells:
        frontier = frontier_cells()
        if not frontier:
            return None
        sample_count = min(12, len(frontier))
        best_cell = frontier[rng.randrange(0, len(frontier))]
        best_score = -1e9
        for _ in range(sample_count):
            cell = frontier[rng.randrange(0, len(frontier))]
            x, y = cell
            neighbors = open_neighbors_count(board, x, y)
            node_dist = nearest_node_distance(x, y, mission.nodes)
            score = (
                rng.uniform(-0.18, 0.18)
                + (2.6 - abs(2 - neighbors)) * 0.42
                - node_dist * 0.01
            )
            if score > best_score:
                best_score = score
                best_cell = cell
        bx, by = best_cell
        board[by][bx] = False
        open_cells.add(best_cell)

    # If too open after carving, close some weakly-constrained cells.
    while len(open_cells) > target_open_cells:
        closable = [
            (x, y)
            for (x, y) in open_cells
            if (x, y) not in required_open and (x, y) != (start_x, start_y)
        ]
        if not closable:
            break
        sample_count = min(20, len(closable))
        best_cell = closable[rng.randrange(0, len(closable))]
        best_score = -1e9
        for _ in range(sample_count):
            cell = closable[rng.randrange(0, len(closable))]
            x, y = cell
            neighbors = open_neighbors_count(board, x, y)
            node_dist = nearest_node_distance(x, y, mission.nodes)
            score = neighbors * 0.7 + node_dist * 0.02 + rng.uniform(-0.15, 0.15)
            if score > best_score:
                best_score = score
                best_cell = cell
        x, y = best_cell
        board[y][x] = True
        open_cells.remove(best_cell)

    for x, y in required_open:
        board[y][x] = False
    for x, y in required_blocked:
        board[y][x] = True
    board[start_y][start_x] = False
    return board


def evaluate_mission_genome(
    v3,
    v4,
    mission: MissionGraph,
    genome,
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
):
    rng = random.Random(level_seed)
    genome = v4.clamp_genome(genome, size, args)

    program_len = genome.state_count * 4
    if program_len < args.min_program_length or program_len > args.max_program_length:
        return v4.EvalOutcome(genome=genome, fitness=-1e9, reject_code="pb", candidate=None)

    next_states = v4.build_next_states(genome.state_count, genome.jump_bias, genome.rewire_ratio, rng)
    turns = v4.build_turns(genome.state_count, genome.turn_left_bias, rng)
    program = v3.build_program(genome.state_count, next_states, turns)

    if core.has_meaningless_jump_instruction(program):
        return v4.EvalOutcome(genome=genome, fitness=-1e9, reject_code="mj", candidate=None)

    if mission.nodes:
        start_x = clamp_int(mission.nodes[0].x, 1, max(1, size - 2))
        start_y = clamp_int(mission.nodes[0].y, 1, max(1, size - 2))
    else:
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
        return v4.EvalOutcome(genome=genome, fitness=-1e9, reject_code="ct", candidate=None)

    if trace.steps < min_steps_required:
        return v4.EvalOutcome(genome=genome, fitness=-1e9, reject_code="ms", candidate=None)
    if trace.jump_exec_count == 0 or (trace.sense_true + trace.sense_false) == 0:
        return v4.EvalOutcome(genome=genome, fitness=-1e9, reject_code="js", candidate=None)
    if trace.sense_true == 0 or trace.sense_false == 0:
        return v4.EvalOutcome(genome=genome, fitness=-1e9, reject_code="sb", candidate=None)

    trace_coverage = len(trace.executed_pcs) / float(len(program))
    if trace_coverage < args.min_instruction_coverage:
        return v4.EvalOutcome(genome=genome, fitness=-1e9, reject_code="ux", candidate=None)

    trace_direction_types = sum(1 for count in trace.direction_move_counts if count > 0)
    if trace_direction_types < args.min_solution_direction_types:
        return v4.EvalOutcome(genome=genome, fitness=-1e9, reject_code="dv", candidate=None)

    trace_spread = v3.route_spread_ratio(size, trace.visited_cells)
    if trace_spread < args.min_route_spread:
        return v4.EvalOutcome(genome=genome, fitness=-1e9, reject_code="sp", candidate=None)
    if len(trace.visited_cells) < min_visited_required:
        return v4.EvalOutcome(genome=genome, fitness=-1e9, reject_code="vc", candidate=None)

    target_open_ratio = v4.compute_target_open_ratio(density_driver_percent, genome)
    target_final_density_percent = 100.0 * (1.0 - target_open_ratio)

    board = build_mission_board(
        size=size,
        mission=mission,
        target_open_ratio=target_open_ratio,
        requirements=trace.requirements,
        start_x=start_x,
        start_y=start_y,
        rng=rng,
    )
    if board is None:
        return v4.EvalOutcome(genome=genome, fitness=-1e9, reject_code="dn", candidate=None)

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
        return v4.EvalOutcome(genome=genome, fitness=-1e9, reject_code="ne", candidate=None)

    if args.max_straight_run > 0 and core.has_straight_run_at_least(
        level,
        program,
        args.max_straight_run,
        generation_execution_limit,
    ):
        return v4.EvalOutcome(genome=genome, fitness=-1e9, reject_code="sr", candidate=None)

    has_turn_cancel, has_dead_instruction = core.analyze_execution_path(
        level,
        program,
        generation_execution_limit,
    )
    if has_turn_cancel:
        return v4.EvalOutcome(genome=genome, fitness=-1e9, reject_code="tc", candidate=None)
    if has_dead_instruction:
        return v4.EvalOutcome(genome=genome, fitness=-1e9, reject_code="ux", candidate=None)

    if core.has_easy_two_direction_program(level):
        return v4.EvalOutcome(genome=genome, fitness=-1e9, reject_code="pl", candidate=None)

    min_moves_to_exit = core.minimum_moves_to_exit(level)
    if min_moves_to_exit is None:
        return v4.EvalOutcome(genome=genome, fitness=-1e9, reject_code="np", candidate=None)
    min_direction_types_to_exit = core.minimum_distinct_directions_to_exit(level)
    if min_direction_types_to_exit is None:
        return v4.EvalOutcome(genome=genome, fitness=-1e9, reject_code="np", candidate=None)
    if min_direction_types_to_exit < args.min_direction_types_to_exit:
        return v4.EvalOutcome(genome=genome, fitness=-1e9, reject_code="md", candidate=None)

    solution_hash = core.compute_program_hash(program)
    level.solution_hash = solution_hash

    sealed_unreachable_cells = 0
    if args.seal_unreachable:
        sealed_unreachable_cells = core.seal_unreachable_cells(level)
        post_seal_run = core.simulate_program(level, program, generation_execution_limit)
        if post_seal_run.outcome != "escape":
            return v4.EvalOutcome(genome=genome, fitness=-1e9, reject_code="ne", candidate=None)
        run_result = post_seal_run
        min_moves_to_exit = core.minimum_moves_to_exit(level)
        if min_moves_to_exit is None:
            return v4.EvalOutcome(genome=genome, fitness=-1e9, reject_code="np", candidate=None)
        min_direction_types_to_exit = core.minimum_distinct_directions_to_exit(level)
        if min_direction_types_to_exit is None:
            return v4.EvalOutcome(genome=genome, fitness=-1e9, reject_code="np", candidate=None)
        if min_direction_types_to_exit < args.min_direction_types_to_exit:
            return v4.EvalOutcome(genome=genome, fitness=-1e9, reject_code="md", candidate=None)

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

    placeholder = v4.Candidate(
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

    feature = v4.candidate_feature_vector(placeholder, size)
    novelty = v4.novelty_score(feature, hall_features, args.novelty_neighbors)
    fitness = raw_score + novelty * args.novelty_weight

    candidate = v4.Candidate(
        level_seed=placeholder.level_seed,
        attempt_index=placeholder.attempt_index,
        fitness=fitness,
        raw_score=placeholder.raw_score,
        level=placeholder.level,
        level_text=placeholder.level_text,
        level_hash=placeholder.level_hash,
        solution=placeholder.solution,
        solution_steps=placeholder.solution_steps,
        min_moves_to_exit=placeholder.min_moves_to_exit,
        min_direction_types_to_exit=placeholder.min_direction_types_to_exit,
        instruction_coverage=placeholder.instruction_coverage,
        visited_cell_count=placeholder.visited_cell_count,
        spread_ratio=placeholder.spread_ratio,
        direction_types_used=placeholder.direction_types_used,
        sense_true=placeholder.sense_true,
        sense_false=placeholder.sense_false,
        jump_exec_count=placeholder.jump_exec_count,
        generation_execution_limit=placeholder.generation_execution_limit,
        sealed_unreachable_cells=placeholder.sealed_unreachable_cells,
        texture_cleanup_attempts=placeholder.texture_cleanup_attempts,
        texture_flips_applied=placeholder.texture_flips_applied,
        checkerboard_2x2=placeholder.checkerboard_2x2,
        target_density_percent=placeholder.target_density_percent,
        density_driver_percent=placeholder.density_driver_percent,
        final_density_percent=placeholder.final_density_percent,
        novelty=novelty,
        genome=placeholder.genome,
    )

    return v4.EvalOutcome(genome=genome, fitness=fitness, reject_code=None, candidate=candidate)


def random_counterexample_jump_offset(length: int, rng: random.Random) -> int:
    if length <= 1:
        return 2
    candidates: list[int] = []
    max_jump = max(1, min(length - 1, 8))
    for distance in range(1, max_jump + 1):
        for sign in (-1, 1):
            offset = sign * distance
            effective = offset % length
            if effective in (0, 1):
                continue
            candidates.append(offset)
    if not candidates:
        return 2
    return candidates[rng.randrange(0, len(candidates))]


def random_counterexample_program(length: int, rng: random.Random) -> list[core.Instruction]:
    length = max(1, length)
    program: list[core.Instruction] = []
    for _ in range(length):
        roll = rng.random()
        if roll < 0.40:
            program.append(core.Instruction("F", 1))
        elif roll < 0.58:
            program.append(core.Instruction("S", 1))
        elif roll < 0.72:
            program.append(core.Instruction("L", 1))
        elif roll < 0.86:
            program.append(core.Instruction("R", 1))
        else:
            program.append(core.Instruction("J", random_counterexample_jump_offset(length, rng)))

    if not any(inst.op == "F" for inst in program):
        program[rng.randrange(0, length)] = core.Instruction("F", 1)

    for index, inst in enumerate(program):
        if inst.op != "J":
            continue
        offset = inst.arg if isinstance(inst.arg, int) else 1
        effective = offset % length if length > 0 else 0
        if effective in (0, 1):
            program[index] = core.Instruction("J", random_counterexample_jump_offset(length, rng))

    if core.has_meaningless_jump_instruction(program):
        for index, inst in enumerate(program):
            if inst.op == "J":
                program[index] = core.Instruction("J", random_counterexample_jump_offset(length, rng))
    return program


def survives_counterexample_search(level: core.Level, args: argparse.Namespace, rng: random.Random) -> tuple[bool, int]:
    if args.cegis_rounds <= 0 or args.cegis_attempts <= 0:
        return True, 0

    total_evaluations = 0
    max_len = min(args.cegis_short_program_len, level.program_limit)
    if max_len < 1:
        return True, 0

    for _ in range(args.cegis_rounds):
        for _ in range(args.cegis_attempts):
            total_evaluations += 1
            length = rng.randint(1, max_len)
            program = random_counterexample_program(length, rng)
            result = core.simulate_program(level, program, level.execution_limit)
            if result.outcome == "escape":
                return False, total_evaluations

    return True, total_evaluations


def mission_bonus_score(mission: MissionGraph, args: argparse.Namespace) -> float:
    ambiguity_reward = mission.branching_ratio * 420.0 * args.ambiguity_target
    decoy_reward = mission.decoy_edges * 58.0
    loop_reward = mission.loop_edges * 72.0
    spread_reward = mission.spread_ratio * 900.0
    cycle_reward = mission.cycle_depth_est * 44.0
    return ambiguity_reward + decoy_reward + loop_reward + spread_reward + cycle_reward


def has_meaningless_jump_for_seed(v3, v4, genome, level_seed: int) -> bool:
    rng = random.Random(level_seed)
    next_states = v4.build_next_states(genome.state_count, genome.jump_bias, genome.rewire_ratio, rng)
    turns = v4.build_turns(genome.state_count, genome.turn_left_bias, rng)
    program = v3.build_program(genome.state_count, next_states, turns)
    return core.has_meaningless_jump_instruction(program)


def build_solution_payload(
    level_number: int,
    record: CandidateRecord,
    series_config: dict[str, object],
    mission_config: dict[str, object],
) -> dict[str, object]:
    candidate = record.candidate
    level = candidate.level
    mission = record.mission

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
            "mode": "v7_mission_compiler",
            "seed": record.level_seed,
            "attempts_used": record.attempt_index,
            "series_config": series_config,
            "mission_config": mission_config,
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
            "mission_bonus": round(record.mission_bonus, 3),
            "combined_score": round(record.combined_score, 3),
            "cegis_evaluations": record.cegis_evaluations,
            "mission": {
                "nodes": len(mission.nodes),
                "main_edges": mission.main_edges,
                "loop_edges": mission.loop_edges,
                "decoy_edges": mission.decoy_edges,
                "cycle_depth_est": mission.cycle_depth_est,
                "spread_ratio": round(mission.spread_ratio, 4),
                "branching_ratio": round(mission.branching_ratio, 4),
                "recipe": record.mission_recipe,
            },
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
            "V7 mission-compiler skeleton. Synthesizes a mission graph, compiles it into a "
            "generator recipe, validates with v4 constraints, and scores by mission + solution quality."
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

    parser.add_argument("--center-jitter", type=int, default=8, help="Base center jitter for start position.")
    parser.add_argument("--state-visit-cap", type=int, default=40, help="Base guided state visit cap.")
    parser.add_argument("--generation-execution-limit", type=int, default=0, help="Generation execution cap (0=auto).")

    parser.add_argument("--min-reachable-open-ratio", type=float, default=0.10, help="Min compiled reachable-open ratio.")
    parser.add_argument("--max-reachable-open-ratio", type=float, default=0.42, help="Max compiled reachable-open ratio.")

    parser.add_argument("--min-instruction-coverage", type=float, default=0.86, help="Min executed instruction coverage.")
    parser.add_argument("--min-route-spread", type=float, default=0.08, help="Min route spread ratio.")
    parser.add_argument("--min-visited-size-factor", type=float, default=0.5, help="Min visited cells threshold = size*factor.")
    parser.add_argument("--min-steps-per-size", type=float, default=1.5, help="Min steps threshold = size*factor.")
    parser.add_argument("--min-solution-direction-types", type=int, default=3, help="Min direction types used by solution.")
    parser.add_argument("--max-straight-run", type=int, default=0, help="Reject straight run >= N (0 disables).")
    parser.add_argument("--min-direction-types-to-exit", type=int, default=2, help="Min direction types needed to exit.")

    parser.add_argument("--best-of", type=int, default=3, help="Valid candidates required per level.")
    parser.add_argument("--candidate-attempts", type=int, default=700, help="Hard cap on attempts per level (0=none).")

    parser.add_argument("--mission-nodes-min", type=int, default=5, help="Minimum mission graph nodes.")
    parser.add_argument("--mission-nodes-max", type=int, default=12, help="Maximum mission graph nodes.")
    parser.add_argument("--loop-depth-target", type=int, default=3, help="Target number of loop-back edges.")
    parser.add_argument("--decoy-depth-target", type=int, default=4, help="Target number of decoy edges.")
    parser.add_argument("--ambiguity-target", type=float, default=0.55, help="Ambiguity target in [0,1] for recipe compilation.")
    parser.add_argument("--mission-spread-target", type=float, default=0.40, help="Target mission spread ratio for placement.")

    parser.add_argument("--cegis-rounds", type=int, default=0, help="Short-program counterexample rounds (0 disables).")
    parser.add_argument("--cegis-attempts", type=int, default=60, help="Counterexample attempts per round.")
    parser.add_argument("--cegis-short-program-len", type=int, default=8, help="Max short program length for CEGIS checks.")

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
    if args.mission_nodes_min < 3 or args.mission_nodes_max < args.mission_nodes_min:
        return False, "mission node bounds are invalid"
    if args.loop_depth_target < 0 or args.decoy_depth_target < 0:
        return False, "loop/decoy targets must be >= 0"
    if args.ambiguity_target < 0.0 or args.ambiguity_target > 1.0:
        return False, "--ambiguity-target must be in [0,1]"
    if args.mission_spread_target <= 0.0 or args.mission_spread_target > 1.0:
        return False, "--mission-spread-target must be in (0,1]"
    if args.cegis_rounds < 0 or args.cegis_attempts < 0 or args.cegis_short_program_len < 1:
        return False, "CEGIS options are invalid"
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
        print(f"Error: could not load helper modules: {exc}", file=sys.stderr)
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
        f"Generating V7-mission levels {args.start_level}..{args.max_level} "
        f"(seed={args.seed}, out={args.out_dir}, solutions={args.solution_dir}, "
        f"size={'fixed '+str(args.size) if args.size is not None else str(args.min_size)+'->'+str(args.max_size)}, "
        f"program_len={args.min_program_length}..{args.max_program_length}, "
        f"density_driver={args.min_density:.1f}%..{args.max_density:.1f}%, "
        f"mission_nodes={args.mission_nodes_min}..{args.mission_nodes_max}, "
        f"loop_target={args.loop_depth_target}, decoy_target={args.decoy_depth_target}, "
        f"ambiguity_target={args.ambiguity_target:.2f}, best_of={args.best_of}, "
        f"candidate_attempts={attempts_text}, cegis_rounds={args.cegis_rounds}, "
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

    mission_config = {
        "mission_nodes_min": args.mission_nodes_min,
        "mission_nodes_max": args.mission_nodes_max,
        "loop_depth_target": args.loop_depth_target,
        "decoy_depth_target": args.decoy_depth_target,
        "ambiguity_target": args.ambiguity_target,
        "mission_spread_target": args.mission_spread_target,
        "cegis_rounds": args.cegis_rounds,
        "cegis_attempts": args.cegis_attempts,
        "cegis_short_program_len": args.cegis_short_program_len,
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
        attempts = 0
        evaluations = 0
        reject_counts: dict[str, int] = {}
        hall_by_hash: dict[str, CandidateRecord] = {}

        if args.verbose:
            print(
                f"Level {level_number} constraints: size={size}x{size}, target_program_len={target_program_length}, "
                f"density_driver={density_driver_percent:.1f}%, min_steps={min_steps_required}, "
                f"min_visited={min_visited_required}, min_spread={args.min_route_spread:.2f}, "
                f"min_solution_dir_types={args.min_solution_direction_types}, "
                f"min_exit_dir_types={args.min_direction_types_to_exit}, best_of={args.best_of}, "
                f"candidate_attempts={attempts_text}, mission_nodes={args.mission_nodes_min}..{args.mission_nodes_max}, "
                f"loop_target={args.loop_depth_target}, decoy_target={args.decoy_depth_target}, "
                f"ambiguity_target={args.ambiguity_target:.2f}, cegis_rounds={args.cegis_rounds}"
            )

        best_combined = -1e12

        while len(hall_by_hash) < args.best_of and (
            args.candidate_attempts == 0 or attempts < args.candidate_attempts
        ):
            attempts += 1
            level_seed = batch_rng.randrange(0, 2**63)
            rng = random.Random(level_seed)

            mission = synthesize_mission_graph(size=size, args=args, rng=rng)
            if mission is None:
                reject_counts["mg"] = reject_counts.get("mg", 0) + 1
                continue

            try:
                genome, recipe = compile_mission_to_genome(
                    v4=v4,
                    mission=mission,
                    size=size,
                    target_program_length=target_program_length,
                    density_driver_percent=density_driver_percent,
                    args=args,
                    rng=rng,
                )
            except Exception:  # noqa: BLE001
                reject_counts["mc"] = reject_counts.get("mc", 0) + 1
                continue

            if has_meaningless_jump_for_seed(v3=v3, v4=v4, genome=genome, level_seed=level_seed):
                reject_counts["mj"] = reject_counts.get("mj", 0) + 1
                continue

            evaluations += 1
            hall_features = [
                v4.candidate_feature_vector(item.candidate, size)
                for item in hall_by_hash.values()
            ]

            outcome = evaluate_mission_genome(
                v3=v3,
                v4=v4,
                mission=mission,
                genome=genome,
                level_number=level_number,
                size=size,
                target_program_length=target_program_length,
                density_driver_percent=density_driver_percent,
                min_steps_required=min_steps_required,
                min_visited_required=min_visited_required,
                args=args,
                level_seed=level_seed,
                attempt_index=attempts,
                hall_features=hall_features,
            )

            if outcome.candidate is None:
                code = outcome.reject_code or "??"
                reject_counts[code] = reject_counts.get(code, 0) + 1
                status = f"rejected({code})"
            else:
                candidate = outcome.candidate
                survives_cegis, cegis_evals = survives_counterexample_search(candidate.level, args, rng)
                if not survives_cegis:
                    reject_counts["ce"] = reject_counts.get("ce", 0) + 1
                    status = "rejected(ce)"
                else:
                    bonus = mission_bonus_score(mission, args)
                    combined_score = candidate.fitness + bonus
                    record = CandidateRecord(
                        candidate=candidate,
                        level_seed=level_seed,
                        attempt_index=attempts,
                        mission=mission,
                        mission_recipe=recipe,
                        mission_bonus=bonus,
                        combined_score=combined_score,
                        cegis_evaluations=cegis_evals,
                    )
                    existing = hall_by_hash.get(candidate.level_hash)
                    if existing is None or record.combined_score > existing.combined_score:
                        hall_by_hash[candidate.level_hash] = record
                    best_combined = max(best_combined, record.combined_score)
                    status = "candidate_ok"

            if show_live_progress:
                attempts_value = f"{attempts:>{attempts_width}d}"
                progress_width = update_progress_line(
                    f"Level {level_number}/{args.max_level}: attempts={attempts_value}/{attempts_text}, "
                    f"evals={evaluations}, best_of={min(len(hall_by_hash), args.best_of)}/{args.best_of}, "
                    f"best_combined={best_combined:.1f}, status={status}, {format_reject_counts(reject_counts)}",
                    progress_width,
                    show_live_progress,
                )

        clear_progress_line(progress_width, show_live_progress)

        hall_candidates = sorted(hall_by_hash.values(), key=lambda item: item.combined_score, reverse=True)
        if len(hall_candidates) < args.best_of:
            print(
                f"Error generating level {level_number}: found {len(hall_candidates)}/{args.best_of} valid candidates "
                f"after {attempts} attempts and {evaluations} evaluations "
                f"(rejects: {format_reject_counts(reject_counts)}).",
                file=sys.stderr,
            )
            return 2

        chosen = hall_candidates[0]
        payload = build_solution_payload(
            level_number=level_number,
            record=chosen,
            series_config=series_config,
            mission_config=mission_config,
        )

        level_path = args.out_dir / f"{level_number}.level"
        solution_path = args.solution_dir / f"{level_number}.solution.json"
        level_path.write_text(chosen.candidate.level_text + "\n", encoding="utf-8")
        solution_path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")

        mission = chosen.mission
        print(
            f"Level {level_number}: ok "
            f"(size={chosen.candidate.level.width}x{chosen.candidate.level.height}, "
            f"plim={chosen.candidate.level.program_limit}, elim={chosen.candidate.level.execution_limit}, "
            f"attempts={chosen.attempt_index}, evals={evaluations}, best_of={min(len(hall_candidates), args.best_of)}/{args.best_of}, "
            f"solution_steps={chosen.candidate.solution_steps}, visited={chosen.candidate.visited_cell_count}, "
            f"spread={chosen.candidate.spread_ratio:.3f}, direction_types_used={chosen.candidate.direction_types_used}, "
            f"density_driver={chosen.candidate.density_driver_percent:.1f}%, "
            f"density_target={chosen.candidate.target_density_percent:.1f}%, density_final={chosen.candidate.final_density_percent:.1f}%, "
            f"mission_nodes={len(mission.nodes)}, loop_edges={mission.loop_edges}, decoy_edges={mission.decoy_edges}, "
            f"mission_spread={mission.spread_ratio:.3f}, mission_branch={mission.branching_ratio:.3f}, "
            f"cegis_evals={chosen.cegis_evaluations}, min_moves_to_exit={chosen.candidate.min_moves_to_exit}, "
            f"min_direction_types_to_exit={chosen.candidate.min_direction_types_to_exit}, "
            f"score={chosen.candidate.fitness:.1f}, combined={chosen.combined_score:.1f})"
        )
        if args.verbose:
            print(f"Level {level_number} solution_program: {core.format_program(chosen.candidate.solution)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
