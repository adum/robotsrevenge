#!/usr/bin/env python3
from __future__ import annotations

import argparse
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
)


@dataclass
class ProgramBlueprint:
    state_count: int
    next_states: tuple[int, ...]
    turns: tuple[str, ...]
    program: list[core.Instruction]


@dataclass
class ConstraintTrace:
    requirements: dict[tuple[int, int], bool]
    steps: int
    visited_cells: set[tuple[int, int]]
    executed_pcs: set[int]
    jump_exec_count: int
    sense_true: int
    sense_false: int
    direction_move_counts: tuple[int, int, int, int]


@dataclass
class MoveOption:
    branch: str
    escape: bool
    next_x: int
    next_y: int
    next_dir: int
    step_cost: int
    score: float
    updates: list[tuple[int, int, bool]]


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


def choose_target_open_ratio(base_density_percent: float, args: argparse.Namespace) -> float:
    base_open_ratio = 1.0 - (base_density_percent / 100.0)
    scaled_open_ratio = base_open_ratio * args.reachable_open_scale
    return clamp_float(
        scaled_open_ratio,
        args.min_reachable_open_ratio,
        args.max_reachable_open_ratio,
    )


def choose_start(size: int, center_jitter: int, rng: random.Random) -> tuple[int, int]:
    min_coord = 1
    max_coord = size - 2
    cx = clamp_int(size // 2, min_coord, max_coord)
    cy = clamp_int(size // 2, min_coord, max_coord)
    if center_jitter <= 0:
        return cx, cy

    max_j = min(center_jitter, max(0, size // 5))
    offsets = list(range(-max_j, max_j + 1))
    ox = offsets[rng.randrange(0, len(offsets))]
    oy = offsets[rng.randrange(0, len(offsets))]
    sx = clamp_int(cx + ox, min_coord, max_coord)
    sy = clamp_int(cy + oy, min_coord, max_coord)
    return sx, sy


def ordered_state_counts(target_program_length: int, min_program_length: int, max_program_length: int) -> list[int]:
    min_states = max(5, math.ceil(min_program_length / 4.0))
    max_states = max(min_states, math.floor(max_program_length / 4.0))
    center = clamp_int(int(round(target_program_length / 4.0)), min_states, max_states)
    states = list(range(min_states, max_states + 1))
    states.sort(key=lambda value: (abs(value - center), value))
    return states


def build_program(
    state_count: int,
    next_states: tuple[int, ...],
    turns: tuple[str, ...],
) -> list[core.Instruction]:
    program: list[core.Instruction] = []
    length = state_count * 4

    for state_index in range(state_count):
        turn_op = turns[state_index]
        next_state = next_states[state_index]

        base_pc = state_index * 4
        jump_pc = base_pc + 3
        destination_pc = next_state * 4
        offset = destination_pc - jump_pc

        if offset > length // 2:
            offset -= length
        elif offset < -(length // 2):
            offset += length

        if offset == 0:
            offset = (next_state - state_index) * 4
            if offset == 0:
                offset = 2

        program.append(core.Instruction("S", 1))
        program.append(core.Instruction(turn_op, 1))
        program.append(core.Instruction("F", 1))
        program.append(core.Instruction("J", offset))

    return program


def generate_blueprint(
    target_program_length: int,
    min_program_length: int,
    max_program_length: int,
    rng: random.Random,
) -> ProgramBlueprint | None:
    for state_count in ordered_state_counts(target_program_length, min_program_length, max_program_length):
        for _ in range(120):
            cycle = list(range(state_count))
            rng.shuffle(cycle)
            next_states = [0 for _ in range(state_count)]
            for i, state in enumerate(cycle):
                next_states[state] = cycle[(i + 1) % state_count]
            next_states_tuple = tuple(next_states)

            turns_list = ["L" if rng.random() < 0.5 else "R" for _ in range(state_count)]
            if all(turn == "L" for turn in turns_list):
                turns_list[rng.randrange(0, state_count)] = "R"
            if all(turn == "R" for turn in turns_list):
                turns_list[rng.randrange(0, state_count)] = "L"
            turns = tuple(turns_list)

            program = build_program(state_count, next_states_tuple, turns)
            if len(program) < min_program_length or len(program) > max_program_length:
                continue
            if core.has_meaningless_jump_instruction(program):
                continue

            return ProgramBlueprint(
                state_count=state_count,
                next_states=next_states_tuple,
                turns=turns,
                program=program,
            )

    return None


def route_spread_ratio(size: int, visited_cells: set[tuple[int, int]]) -> float:
    if not visited_cells or size <= 0:
        return 0.0
    min_x = min(x for x, _ in visited_cells)
    max_x = max(x for x, _ in visited_cells)
    min_y = min(y for _, y in visited_cells)
    max_y = max(y for _, y in visited_cells)
    bbox_area = (max_x - min_x + 1) * (max_y - min_y + 1)
    return bbox_area / float(size * size)


def move_option_score(
    option_branch: str,
    escape: bool,
    destination: tuple[int, int] | None,
    destination_dir: int,
    min_steps: int,
    current_steps: int,
    size: int,
    cell_visits: dict[tuple[int, int], int],
    visited_cells: set[tuple[int, int]],
    bbox: tuple[int, int, int, int],
    previous_cell: tuple[int, int] | None,
    sense_true: int,
    sense_false: int,
    rng: random.Random,
) -> float:
    score = rng.uniform(-0.14, 0.14)

    if escape:
        score += 9.0
        score += max(0.0, (current_steps - min_steps) * 0.002)
        return score

    if destination is None:
        return -9999.0

    dx, dy = destination
    visit_count = cell_visits.get(destination, 0)
    if visit_count == 0:
        score += 2.0
    else:
        score -= 1.0 * visit_count

    min_x, max_x, min_y, max_y = bbox
    old_area = (max_x - min_x + 1) * (max_y - min_y + 1)
    new_min_x = min(min_x, dx)
    new_max_x = max(max_x, dx)
    new_min_y = min(min_y, dy)
    new_max_y = max(max_y, dy)
    new_area = (new_max_x - new_min_x + 1) * (new_max_y - new_min_y + 1)
    score += (new_area - old_area) * 0.28

    edge_distance = min(dx, size - 1 - dx, dy, size - 1 - dy)
    if current_steps < min_steps:
        score += edge_distance * 0.015
    else:
        score += (size / 2.0 - edge_distance) * 0.03

    if previous_cell is not None and destination == previous_cell:
        score -= 1.8

    if option_branch == "blocked":
        if sense_true <= sense_false:
            score += 0.18
        else:
            score -= 0.06
    else:
        if sense_false <= sense_true:
            score += 0.18
        else:
            score -= 0.06

    score += 0.01 * destination_dir
    return score


def apply_updates(
    requirements: dict[tuple[int, int], bool],
    updates: list[tuple[int, int, bool]],
) -> bool:
    for x, y, blocked in updates:
        existing = requirements.get((x, y))
        if existing is not None and existing != blocked:
            return False
    for x, y, blocked in updates:
        requirements[(x, y)] = blocked
    return True


def build_guided_trace(
    blueprint: ProgramBlueprint,
    start_x: int,
    start_y: int,
    size: int,
    min_steps_required: int,
    max_steps: int,
    state_visit_cap: int,
    rng: random.Random,
) -> ConstraintTrace | None:
    requirements: dict[tuple[int, int], bool] = {(start_x, start_y): False}
    visited_cells: set[tuple[int, int]] = {(start_x, start_y)}
    executed_pcs: set[int] = set()
    state_visits: dict[tuple[int, int, int, int], int] = {}
    cell_visits: dict[tuple[int, int], int] = {(start_x, start_y): 1}

    x = start_x
    y = start_y
    dir_index = core.NORTH_DIR
    state_index = 0
    steps = 0
    jump_exec_count = 0
    sense_true = 0
    sense_false = 0
    direction_move_counts = [0, 0, 0, 0]
    previous_cell: tuple[int, int] | None = None

    min_x = start_x
    max_x = start_x
    min_y = start_y
    max_y = start_y

    while steps < max_steps:
        visit_key = (x, y, dir_index, state_index)
        visit_count = state_visits.get(visit_key, 0) + 1
        state_visits[visit_key] = visit_count
        if visit_count > state_visit_cap:
            return None

        turn_op = blueprint.turns[state_index]
        turn_delta = -1 if turn_op == "L" else 1
        dx, dy = core.DIR_DELTAS[dir_index]
        ahead_x = x + dx
        ahead_y = y + dy

        options: list[MoveOption] = []

        # Clear branch: S false -> skip turn -> F
        updates_clear: list[tuple[int, int, bool]] = []
        if not core.in_bounds(ahead_x, ahead_y, size, size):
            step_cost = 2
            if steps + step_cost <= max_steps and steps + step_cost >= min_steps_required:
                score = move_option_score(
                    option_branch="clear",
                    escape=True,
                    destination=None,
                    destination_dir=dir_index,
                    min_steps=min_steps_required,
                    current_steps=steps,
                    size=size,
                    cell_visits=cell_visits,
                    visited_cells=visited_cells,
                    bbox=(min_x, max_x, min_y, max_y),
                    previous_cell=previous_cell,
                    sense_true=sense_true,
                    sense_false=sense_false,
                    rng=rng,
                )
                options.append(
                    MoveOption(
                        branch="clear",
                        escape=True,
                        next_x=ahead_x,
                        next_y=ahead_y,
                        next_dir=dir_index,
                        step_cost=step_cost,
                        score=score,
                        updates=updates_clear,
                    )
                )
        else:
            forced = requirements.get((ahead_x, ahead_y))
            if forced is not True:
                updates_clear.append((ahead_x, ahead_y, False))
                destination = (ahead_x, ahead_y)
                step_cost = 3
                if steps + step_cost <= max_steps:
                    score = move_option_score(
                        option_branch="clear",
                        escape=False,
                        destination=destination,
                        destination_dir=dir_index,
                        min_steps=min_steps_required,
                        current_steps=steps,
                        size=size,
                        cell_visits=cell_visits,
                        visited_cells=visited_cells,
                        bbox=(min_x, max_x, min_y, max_y),
                        previous_cell=previous_cell,
                        sense_true=sense_true,
                        sense_false=sense_false,
                        rng=rng,
                    )
                    options.append(
                        MoveOption(
                            branch="clear",
                            escape=False,
                            next_x=destination[0],
                            next_y=destination[1],
                            next_dir=dir_index,
                            step_cost=step_cost,
                            score=score,
                            updates=updates_clear,
                        )
                    )

        # Blocked branch: S true -> turn -> F
        if core.in_bounds(ahead_x, ahead_y, size, size):
            forced_ahead = requirements.get((ahead_x, ahead_y))
            if forced_ahead is not False:
                turned_dir = core.wrap(dir_index + turn_delta, 4)
                tdx, tdy = core.DIR_DELTAS[turned_dir]
                turned_x = x + tdx
                turned_y = y + tdy

                updates_blocked: list[tuple[int, int, bool]] = [(ahead_x, ahead_y, True)]

                if not core.in_bounds(turned_x, turned_y, size, size):
                    step_cost = 3
                    if steps + step_cost <= max_steps and steps + step_cost >= min_steps_required:
                        score = move_option_score(
                            option_branch="blocked",
                            escape=True,
                            destination=None,
                            destination_dir=turned_dir,
                            min_steps=min_steps_required,
                            current_steps=steps,
                            size=size,
                            cell_visits=cell_visits,
                            visited_cells=visited_cells,
                            bbox=(min_x, max_x, min_y, max_y),
                            previous_cell=previous_cell,
                            sense_true=sense_true,
                            sense_false=sense_false,
                            rng=rng,
                        )
                        options.append(
                            MoveOption(
                                branch="blocked",
                                escape=True,
                                next_x=turned_x,
                                next_y=turned_y,
                                next_dir=turned_dir,
                                step_cost=step_cost,
                                score=score,
                                updates=updates_blocked,
                            )
                        )
                else:
                    forced_turned = requirements.get((turned_x, turned_y))
                    if forced_turned is not True:
                        updates_blocked.append((turned_x, turned_y, False))
                        destination = (turned_x, turned_y)
                        step_cost = 4
                        if steps + step_cost <= max_steps:
                            score = move_option_score(
                                option_branch="blocked",
                                escape=False,
                                destination=destination,
                                destination_dir=turned_dir,
                                min_steps=min_steps_required,
                                current_steps=steps,
                                size=size,
                                cell_visits=cell_visits,
                                visited_cells=visited_cells,
                                bbox=(min_x, max_x, min_y, max_y),
                                previous_cell=previous_cell,
                                sense_true=sense_true,
                                sense_false=sense_false,
                                rng=rng,
                            )
                            options.append(
                                MoveOption(
                                    branch="blocked",
                                    escape=False,
                                    next_x=destination[0],
                                    next_y=destination[1],
                                    next_dir=turned_dir,
                                    step_cost=step_cost,
                                    score=score,
                                    updates=updates_blocked,
                                )
                            )

        if not options:
            return None

        options.sort(key=lambda item: item.score, reverse=True)
        pick_index = 0
        if len(options) > 1 and rng.random() < 0.08:
            pick_index = 1
        option = options[pick_index]

        if not apply_updates(requirements, option.updates):
            return None

        base_pc = state_index * 4
        executed_pcs.add(base_pc)

        if option.branch == "blocked":
            sense_true += 1
            executed_pcs.add(base_pc + 1)
        else:
            sense_false += 1

        executed_pcs.add(base_pc + 2)
        direction_move_counts[option.next_dir] += 1

        steps += option.step_cost

        if option.escape:
            return ConstraintTrace(
                requirements=requirements,
                steps=steps,
                visited_cells=visited_cells,
                executed_pcs=executed_pcs,
                jump_exec_count=jump_exec_count,
                sense_true=sense_true,
                sense_false=sense_false,
                direction_move_counts=(
                    direction_move_counts[0],
                    direction_move_counts[1],
                    direction_move_counts[2],
                    direction_move_counts[3],
                ),
            )

        executed_pcs.add(base_pc + 3)
        jump_exec_count += 1

        previous_cell = (x, y)
        x = option.next_x
        y = option.next_y
        dir_index = option.next_dir

        visited_cells.add((x, y))
        cell_visits[(x, y)] = cell_visits.get((x, y), 0) + 1
        min_x = min(min_x, x)
        max_x = max(max_x, x)
        min_y = min(min_y, y)
        max_y = max(max_y, y)

        state_index = blueprint.next_states[state_index]

    return None


def build_board(
    size: int,
    target_open_ratio: float,
    requirements: dict[tuple[int, int], bool],
    start_x: int,
    start_y: int,
    rng: random.Random,
) -> list[list[bool]] | None:
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

    available_cells = total_cells - len(required_blocked)
    if len(required_open) > target_open_cells:
        return None
    if target_open_cells > available_cells:
        return None

    board = [[True for _ in range(size)] for _ in range(size)]
    open_cells: set[tuple[int, int]] = set(required_open)

    frontier_list: list[tuple[int, int]] = []
    frontier_index: dict[tuple[int, int], int] = {}

    def frontier_add(cell: tuple[int, int]) -> None:
        if cell in frontier_index:
            return
        frontier_index[cell] = len(frontier_list)
        frontier_list.append(cell)

    def frontier_pop_index(index: int) -> tuple[int, int]:
        pick = frontier_list[index]
        last = frontier_list[-1]
        frontier_list[index] = last
        frontier_index[last] = index
        frontier_list.pop()
        del frontier_index[pick]
        return pick

    def open_neighbor_count(cell: tuple[int, int]) -> int:
        x, y = cell
        count = 0
        for dx, dy in core.DIR_DELTAS:
            nx = x + dx
            ny = y + dy
            if (nx, ny) in open_cells:
                count += 1
        return count

    for x, y in open_cells:
        for dx, dy in core.DIR_DELTAS:
            nx = x + dx
            ny = y + dy
            if not core.in_bounds(nx, ny, size, size):
                continue
            cell = (nx, ny)
            if cell in open_cells or cell in required_blocked:
                continue
            frontier_add(cell)

    while len(open_cells) < target_open_cells:
        if not frontier_list:
            return None

        sample_count = min(8, len(frontier_list))
        best_index = 0
        best_score = -1e9
        for _ in range(sample_count):
            index = rng.randrange(0, len(frontier_list))
            cell = frontier_list[index]
            adjacency = open_neighbor_count(cell)
            edge_distance = min(cell[0], size - 1 - cell[0], cell[1], size - 1 - cell[1])
            # Favor branchy growth (low adjacency) with enough randomness to keep irregular shape.
            score = rng.uniform(-0.25, 0.25) - (adjacency * 0.55) + (edge_distance * 0.01)
            if score > best_score:
                best_score = score
                best_index = index

        pick = frontier_pop_index(best_index)
        open_cells.add(pick)

        px, py = pick
        for dx, dy in core.DIR_DELTAS:
            nx = px + dx
            ny = py + dy
            if not core.in_bounds(nx, ny, size, size):
                continue
            cell = (nx, ny)
            if cell in open_cells or cell in required_blocked:
                continue
            frontier_add(cell)

    for x, y in open_cells:
        board[y][x] = False
    for x, y in required_blocked:
        board[y][x] = True
    board[start_y][start_x] = False
    return board


def simulate_with_trace(
    level: core.Level,
    program: list[core.Instruction],
    max_steps: int,
) -> tuple[core.RunResult, set[tuple[int, int]], set[int], tuple[int, int, int, int]]:
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
            (0, 0, 0, 0),
        )

    x = level.start_x
    y = level.start_y
    dir_index = core.NORTH_DIR
    pc = 0
    steps = 0
    jump_exec_count = 0
    sense_exec_count = 0
    n = len(program)
    dir_moves = [0, 0, 0, 0]

    while steps < max_steps:
        executed_pcs.add(pc)
        inst = program[pc]
        op = inst.op.upper()

        if op == "F":
            dx, dy = core.DIR_DELTAS[dir_index]
            nx = x + dx
            ny = y + dy
            steps += 1
            dir_moves[dir_index] += 1
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
                    (dir_moves[0], dir_moves[1], dir_moves[2], dir_moves[3]),
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
                    (dir_moves[0], dir_moves[1], dir_moves[2], dir_moves[3]),
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
            (dir_moves[0], dir_moves[1], dir_moves[2], dir_moves[3]),
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
        (dir_moves[0], dir_moves[1], dir_moves[2], dir_moves[3]),
    )


def choose_generation_execution_limit(
    size: int,
    program_len: int,
    configured_limit: int,
) -> int:
    auto_limit = max(2400, int(size * size * 0.62 + program_len * 42))
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
    spread_ratio: float,
    direction_types_used: int,
    sense_true: int,
    sense_false: int,
    jump_exec_count: int,
    generation_execution_limit: int,
    generator_attempts: int,
    sealed_unreachable_cells: int,
    density_driver_percent: float,
    target_density_percent: float,
    final_density_percent: float,
    blueprint_states: int,
    blueprint_next_states: list[int],
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
            "mode": "v3_constraint_walk",
            "seed": level_seed,
            "attempts_used": generator_attempts,
            "series_config": series_config,
            "width": level.width,
            "height": level.height,
            "program_limit": level.program_limit,
            "execution_limit": level.execution_limit,
            "generation_execution_limit": generation_execution_limit,
            "blueprint_states": blueprint_states,
            "blueprint_next_states": blueprint_next_states,
            "instruction_coverage": round(instruction_coverage, 4),
            "visited_cell_count": visited_cell_count,
            "spread_ratio": round(spread_ratio, 4),
            "direction_types_used": direction_types_used,
            "sense_true": sense_true,
            "sense_false": sense_false,
            "jump_exec_count": jump_exec_count,
            "sealed_unreachable_cells": sealed_unreachable_cells,
            "density_driver_percent": round(density_driver_percent, 2),
            "target_density_percent": round(target_density_percent, 2),
            "final_density_percent": round(final_density_percent, 2),
            "score": round(score, 3),
        },
        "created_at": timestamp_now_utc(),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "V3 generator: non-spiral route synthesis using a structured S/J/L/R/F state machine. "
            "Builds a compact looping hidden program, guides branch outcomes to traverse widely, "
            "materializes a board, and keeps best candidates by exploration score."
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
        "--progressive-total-levels",
        type=int,
        default=None,
        help="Total level count for progression curves (default: max_level).",
    )

    parser.add_argument(
        "--min-program-length",
        type=int,
        default=20,
        help="Minimum hidden-solution program length (default: 20).",
    )
    parser.add_argument(
        "--max-program-length",
        type=int,
        default=50,
        help="Maximum hidden-solution program length (default: 50).",
    )
    parser.add_argument(
        "--program-cycles",
        type=float,
        default=2.0,
        help="Oscillation cycles for target program length over progression (default: 2.0).",
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
        "--program-slack",
        type=int,
        default=0,
        help="Extra instructions allowed above hidden solution length (default: 0).",
    )

    parser.add_argument(
        "--min-density",
        type=float,
        default=20.0,
        help="Minimum blocked-cell density percent (default: 20).",
    )
    parser.add_argument(
        "--max-density",
        type=float,
        default=50.0,
        help="Maximum blocked-cell density percent (default: 50).",
    )
    parser.add_argument(
        "--density-cycles",
        type=float,
        default=1.5,
        help="Density oscillation cycles over progression (default: 1.5).",
    )
    parser.add_argument(
        "--density-phase",
        type=float,
        default=1.2,
        help="Density oscillation phase in radians (default: 1.2).",
    )
    parser.add_argument(
        "--density-noise",
        type=float,
        default=2.5,
        help="Density noise amplitude in percent (default: 2.5).",
    )
    parser.add_argument(
        "--reachable-open-scale",
        type=float,
        default=0.55,
        help=(
            "Scale applied to (1-density) to choose connected reachable open area "
            "before sealing (default: 0.55)."
        ),
    )
    parser.add_argument(
        "--min-reachable-open-ratio",
        type=float,
        default=0.14,
        help="Minimum connected reachable open-area ratio (default: 0.14).",
    )
    parser.add_argument(
        "--max-reachable-open-ratio",
        type=float,
        default=0.34,
        help="Maximum connected reachable open-area ratio (default: 0.34).",
    )

    parser.add_argument("--center-jitter", type=int, default=8, help="Max center jitter for start position.")
    parser.add_argument(
        "--state-visit-cap",
        type=int,
        default=40,
        help="Max guided visits per (x,y,dir,state) before rejecting trace (default: 40).",
    )
    parser.add_argument(
        "--generation-execution-limit",
        type=int,
        default=0,
        help="Generation simulation cap (0 = auto based on size/program, default: 0).",
    )

    parser.add_argument(
        "--min-instruction-coverage",
        type=float,
        default=0.86,
        help="Minimum executed instruction coverage ratio (default: 0.86).",
    )
    parser.add_argument(
        "--min-route-spread",
        type=float,
        default=0.08,
        help="Minimum visited-route bounding-box area ratio (default: 0.08).",
    )
    parser.add_argument(
        "--min-visited-size-factor",
        type=float,
        default=0.5,
        help="Minimum visited cells threshold as size*factor (default: 0.5).",
    )
    parser.add_argument(
        "--min-steps-per-size",
        type=float,
        default=1.5,
        help="Minimum steps threshold as size*factor (default: 1.5).",
    )
    parser.add_argument(
        "--min-solution-direction-types",
        type=int,
        default=3,
        help="Minimum distinct absolute movement directions used by hidden solution (1-4, default: 3).",
    )
    parser.add_argument(
        "--max-straight-run",
        type=int,
        default=0,
        help="Reject if hidden solution has a straight run >= this (0 disables, default: 0).",
    )
    parser.add_argument(
        "--min-direction-types-to-exit",
        type=int,
        default=2,
        help="Minimum distinct directions needed by movement-only shortest escape (1-4, default: 2).",
    )

    parser.add_argument(
        "--best-of",
        type=int,
        default=3,
        help="Generate this many valid candidates per level and keep best score (default: 3).",
    )
    parser.add_argument(
        "--candidate-attempts",
        type=int,
        default=500,
        help="Max candidate attempts per level (0 = infinite, default: 500).",
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
    parser.add_argument(
        "--seal-unreachable",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Before saving, mark open cells unreachable from start as blocked "
            "(use --no-seal-unreachable to disable, default: true)."
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
    if args.min_program_length < 20:
        print("Error: --min-program-length must be >= 20.", file=sys.stderr)
        return 2
    if args.max_program_length < args.min_program_length:
        print("Error: --max-program-length must be >= --min-program-length.", file=sys.stderr)
        return 2
    if args.max_program_length > core.MAX_PROGRAM_LIMIT:
        print(f"Error: --max-program-length must be <= {core.MAX_PROGRAM_LIMIT}.", file=sys.stderr)
        return 2
    if args.program_slack < 0:
        print("Error: --program-slack must be >= 0.", file=sys.stderr)
        return 2
    if args.min_density < 0.0 or args.max_density > 100.0 or args.max_density < args.min_density:
        print("Error: density bounds must satisfy 0 <= min <= max <= 100.", file=sys.stderr)
        return 2
    if args.reachable_open_scale <= 0.0:
        print("Error: --reachable-open-scale must be > 0.", file=sys.stderr)
        return 2
    if args.min_reachable_open_ratio <= 0.0 or args.min_reachable_open_ratio > 1.0:
        print("Error: --min-reachable-open-ratio must be in (0,1].", file=sys.stderr)
        return 2
    if args.max_reachable_open_ratio <= 0.0 or args.max_reachable_open_ratio > 1.0:
        print("Error: --max-reachable-open-ratio must be in (0,1].", file=sys.stderr)
        return 2
    if args.max_reachable_open_ratio < args.min_reachable_open_ratio:
        print("Error: --max-reachable-open-ratio must be >= --min-reachable-open-ratio.", file=sys.stderr)
        return 2
    if args.min_instruction_coverage <= 0.0 or args.min_instruction_coverage > 1.0:
        print("Error: --min-instruction-coverage must be in (0,1].", file=sys.stderr)
        return 2
    if args.min_route_spread <= 0.0 or args.min_route_spread > 1.0:
        print("Error: --min-route-spread must be in (0,1].", file=sys.stderr)
        return 2
    if args.min_visited_size_factor < 0.0:
        print("Error: --min-visited-size-factor must be >= 0.", file=sys.stderr)
        return 2
    if args.min_steps_per_size < 0.0:
        print("Error: --min-steps-per-size must be >= 0.", file=sys.stderr)
        return 2
    if args.min_solution_direction_types < 1 or args.min_solution_direction_types > 4:
        print("Error: --min-solution-direction-types must be between 1 and 4.", file=sys.stderr)
        return 2
    if args.min_direction_types_to_exit < 1 or args.min_direction_types_to_exit > 4:
        print("Error: --min-direction-types-to-exit must be between 1 and 4.", file=sys.stderr)
        return 2
    if args.max_straight_run < 0:
        print("Error: --max-straight-run must be >= 0.", file=sys.stderr)
        return 2
    if args.state_visit_cap < 1:
        print("Error: --state-visit-cap must be >= 1.", file=sys.stderr)
        return 2
    if args.best_of < 1:
        print("Error: --best-of must be >= 1.", file=sys.stderr)
        return 2
    if args.candidate_attempts < 0:
        print("Error: --candidate-attempts must be >= 0.", file=sys.stderr)
        return 2
    if args.candidate_attempts > 0 and args.best_of > args.candidate_attempts:
        print("Error: --best-of cannot exceed --candidate-attempts when finite.", file=sys.stderr)
        return 2

    if args.progressive_total_levels is None:
        args.progressive_total_levels = args.max_level
    if args.progressive_total_levels < args.max_level:
        print("Error: --progressive-total-levels must be >= max_level.", file=sys.stderr)
        return 2

    if args.seed is None:
        args.seed = random.SystemRandom().randrange(0, 2**63)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    args.solution_dir.mkdir(parents=True, exist_ok=True)

    batch_rng = random.Random(args.seed)
    show_live_progress = args.verbose and sys.stdout.isatty()
    max_attempts_text = "inf" if args.candidate_attempts == 0 else str(args.candidate_attempts)
    attempts_width = len(max_attempts_text) if args.candidate_attempts > 0 else 8

    print(
        f"Generating V3 levels {args.start_level}..{args.max_level} "
        f"(seed={args.seed}, out={args.out_dir}, solutions={args.solution_dir}, "
        f"size={'fixed '+str(args.size) if args.size is not None else str(args.min_size)+'->'+str(args.max_size)}, "
        f"program_len={args.min_program_length}..{args.max_program_length}, "
        f"density={args.min_density:.1f}%..{args.max_density:.1f}%, "
        f"reachable_open_ratio={args.min_reachable_open_ratio:.2f}..{args.max_reachable_open_ratio:.2f} "
        f"(scale={args.reachable_open_scale:.2f}), best_of={args.best_of}, "
        f"candidate_attempts={max_attempts_text}, min_solution_dir_types={args.min_solution_direction_types}, "
        f"min_exit_dir_types={args.min_direction_types_to_exit}, elim_from_solution_steps={'on' if args.elim_from_solution_steps else 'off'}, "
        f"seal_unreachable={'on' if args.seal_unreachable else 'off'})"
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
    }

    for level_number in range(args.start_level, args.max_level + 1):
        size = choose_size(level_number, args)
        target_program_length = choose_target_program_length(level_number, args, batch_rng)
        density_percent = choose_density_percent(level_number, args, batch_rng)
        target_open_ratio = choose_target_open_ratio(density_percent, args)
        target_final_density_percent = 100.0 * (1.0 - target_open_ratio)

        progress_width = 0
        reject_counts: dict[str, int] = {}
        candidate_pool: list[dict[str, object]] = []
        attempts_used = 0

        min_steps_required = max(40, int(round(size * args.min_steps_per_size)))
        min_visited_required = max(12, int(round(size * args.min_visited_size_factor)))

        if args.verbose:
            print(
                f"Level {level_number} constraints: size={size}x{size}, "
                f"target_program_len={target_program_length}, density_driver={density_percent:.1f}%, "
                f"target_final_density={target_final_density_percent:.1f}%, "
                f"min_steps={min_steps_required}, min_visited={min_visited_required}, "
                f"min_spread={args.min_route_spread:.2f}, min_solution_dir_types={args.min_solution_direction_types}, "
                f"min_exit_dir_types={args.min_direction_types_to_exit}, best_of={args.best_of}, "
                f"candidate_attempts={max_attempts_text}"
            )

        while len(candidate_pool) < args.best_of and (
            args.candidate_attempts == 0 or attempts_used < args.candidate_attempts
        ):
            attempts_used += 1
            level_seed = batch_rng.randrange(0, 2**63)
            rng = random.Random(level_seed)

            start_x, start_y = choose_start(size, args.center_jitter, rng)
            blueprint = generate_blueprint(
                target_program_length=target_program_length,
                min_program_length=args.min_program_length,
                max_program_length=args.max_program_length,
                rng=rng,
            )
            if blueprint is None:
                reject_counts["pb"] = reject_counts.get("pb", 0) + 1
                continue

            program = blueprint.program
            if core.has_meaningless_jump_instruction(program):
                reject_counts["mj"] = reject_counts.get("mj", 0) + 1
                continue

            generation_execution_limit = choose_generation_execution_limit(
                size,
                len(program),
                args.generation_execution_limit,
            )

            trace = build_guided_trace(
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
                reject_counts["ct"] = reject_counts.get("ct", 0) + 1
                if show_live_progress:
                    attempts_value = f"{attempts_used:>{attempts_width}d}"
                    progress_width = update_progress_line(
                        f"Level {level_number}/{args.max_level}: attempts={attempts_value}/{max_attempts_text}, "
                        f"best_of={len(candidate_pool)}/{args.best_of}, status=rejected(ct), "
                        f"{format_reject_counts(reject_counts)}",
                        progress_width,
                        show_live_progress,
                    )
                continue

            if trace.steps < min_steps_required:
                reject_counts["ms"] = reject_counts.get("ms", 0) + 1
                continue
            if trace.jump_exec_count == 0 or (trace.sense_true + trace.sense_false) == 0:
                reject_counts["js"] = reject_counts.get("js", 0) + 1
                continue
            if trace.sense_true == 0 or trace.sense_false == 0:
                reject_counts["sb"] = reject_counts.get("sb", 0) + 1
                continue

            trace_coverage = len(trace.executed_pcs) / float(len(program))
            if trace_coverage < args.min_instruction_coverage:
                reject_counts["ux"] = reject_counts.get("ux", 0) + 1
                continue

            trace_direction_types = sum(1 for count in trace.direction_move_counts if count > 0)
            if trace_direction_types < args.min_solution_direction_types:
                reject_counts["dv"] = reject_counts.get("dv", 0) + 1
                continue

            trace_spread = route_spread_ratio(size, trace.visited_cells)
            if trace_spread < args.min_route_spread:
                reject_counts["sp"] = reject_counts.get("sp", 0) + 1
                continue
            if len(trace.visited_cells) < min_visited_required:
                reject_counts["vc"] = reject_counts.get("vc", 0) + 1
                continue

            board = build_board(
                size=size,
                target_open_ratio=target_open_ratio,
                requirements=trace.requirements,
                start_x=start_x,
                start_y=start_y,
                rng=rng,
            )
            if board is None:
                reject_counts["dn"] = reject_counts.get("dn", 0) + 1
                continue

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

            run_result, visited_cells, executed_pcs, direction_counts = simulate_with_trace(
                level=level,
                program=program,
                max_steps=generation_execution_limit,
            )
            if run_result.outcome != "escape":
                reject_counts["ne"] = reject_counts.get("ne", 0) + 1
                continue

            if args.max_straight_run > 0 and core.has_straight_run_at_least(
                level,
                program,
                args.max_straight_run,
                generation_execution_limit,
            ):
                reject_counts["sr"] = reject_counts.get("sr", 0) + 1
                continue

            has_turn_cancel, has_dead_instruction = core.analyze_execution_path(
                level,
                program,
                generation_execution_limit,
            )
            if has_turn_cancel:
                reject_counts["tc"] = reject_counts.get("tc", 0) + 1
                continue
            if has_dead_instruction:
                reject_counts["ux"] = reject_counts.get("ux", 0) + 1
                continue

            if core.has_easy_two_direction_program(level):
                reject_counts["pl"] = reject_counts.get("pl", 0) + 1
                continue

            min_moves_to_exit = core.minimum_moves_to_exit(level)
            if min_moves_to_exit is None:
                reject_counts["np"] = reject_counts.get("np", 0) + 1
                continue
            min_direction_types_to_exit = core.minimum_distinct_directions_to_exit(level)
            if min_direction_types_to_exit is None:
                reject_counts["np"] = reject_counts.get("np", 0) + 1
                continue
            if min_direction_types_to_exit < args.min_direction_types_to_exit:
                reject_counts["md"] = reject_counts.get("md", 0) + 1
                continue

            solution_hash = core.compute_program_hash(program)
            level.solution_hash = solution_hash

            sealed_unreachable_cells = 0
            if args.seal_unreachable:
                sealed_unreachable_cells = core.seal_unreachable_cells(level)
                post_seal_run = core.simulate_program(level, program, generation_execution_limit)
                if post_seal_run.outcome != "escape":
                    reject_counts["ne"] = reject_counts.get("ne", 0) + 1
                    continue
                run_result = post_seal_run
                min_moves_to_exit = core.minimum_moves_to_exit(level)
                if min_moves_to_exit is None:
                    reject_counts["np"] = reject_counts.get("np", 0) + 1
                    continue
                min_direction_types_to_exit = core.minimum_distinct_directions_to_exit(level)
                if min_direction_types_to_exit is None:
                    reject_counts["np"] = reject_counts.get("np", 0) + 1
                    continue
                if min_direction_types_to_exit < args.min_direction_types_to_exit:
                    reject_counts["md"] = reject_counts.get("md", 0) + 1
                    continue

            final_density_percent = 100.0 * core.block_count(level.board) / float(size * size)

            if args.elim_from_solution_steps:
                level.execution_limit = max(1, run_result.steps)

            level_text = core.format_level(level)
            level_hash = core.compute_level_hash(level)
            instruction_coverage = len(executed_pcs) / float(len(program))
            spread_ratio = route_spread_ratio(size, visited_cells)
            direction_types_used = sum(1 for count in direction_counts if count > 0)
            score = (
                run_result.steps
                + min_moves_to_exit * 5.0
                + len(visited_cells) * 1.7
                + spread_ratio * (size * size) * 0.22
                + direction_types_used * 420.0
                + instruction_coverage * 1000.0
            )

            candidate_pool.append(
                {
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
                    "spread_ratio": spread_ratio,
                    "direction_types_used": direction_types_used,
                    "sense_true": trace.sense_true,
                    "sense_false": trace.sense_false,
                    "jump_exec_count": trace.jump_exec_count,
                    "generation_execution_limit": generation_execution_limit,
                    "sealed_unreachable_cells": sealed_unreachable_cells,
                    "density_driver_percent": density_percent,
                    "target_density_percent": target_final_density_percent,
                    "final_density_percent": final_density_percent,
                    "blueprint_states": blueprint.state_count,
                    "blueprint_next_states": list(blueprint.next_states),
                    "score": score,
                    "attempts_used": attempts_used,
                }
            )

            if show_live_progress:
                attempts_value = f"{attempts_used:>{attempts_width}d}"
                best_score = max(item["score"] for item in candidate_pool)
                progress_width = update_progress_line(
                    f"Level {level_number}/{args.max_level}: attempts={attempts_value}/{max_attempts_text}, "
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
        spread_ratio = float(chosen["spread_ratio"])
        direction_types_used = int(chosen["direction_types_used"])
        sense_true = int(chosen["sense_true"])
        sense_false = int(chosen["sense_false"])
        jump_exec_count = int(chosen["jump_exec_count"])
        generation_execution_limit = int(chosen["generation_execution_limit"])
        sealed_unreachable_cells = int(chosen["sealed_unreachable_cells"])
        density_driver_percent = float(chosen["density_driver_percent"])
        target_density_percent = float(chosen["target_density_percent"])
        final_density_percent = float(chosen["final_density_percent"])
        blueprint_states = int(chosen["blueprint_states"])
        blueprint_next_states = [int(value) for value in chosen["blueprint_next_states"]]
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
            spread_ratio=spread_ratio,
            direction_types_used=direction_types_used,
            sense_true=sense_true,
            sense_false=sense_false,
            jump_exec_count=jump_exec_count,
            generation_execution_limit=generation_execution_limit,
            generator_attempts=generator_attempts,
            sealed_unreachable_cells=sealed_unreachable_cells,
            density_driver_percent=density_driver_percent,
            target_density_percent=target_density_percent,
            final_density_percent=final_density_percent,
            blueprint_states=blueprint_states,
            blueprint_next_states=blueprint_next_states,
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
            f"(size={level.width}x{level.height}, plim={level.program_limit}, elim={level.execution_limit}, "
            f"attempts={generator_attempts}, solution_steps={solution_steps}, visited={visited_cell_count}, "
            f"spread={spread_ratio:.3f}, direction_types_used={direction_types_used}, "
            f"density_driver={density_driver_percent:.1f}%, "
            f"density_target={target_density_percent:.1f}%, density_final={final_density_percent:.1f}%, "
            f"min_moves_to_exit={min_moves_to_exit}, min_direction_types_to_exit={min_direction_types_to_exit}, "
            f"blueprint_states={blueprint_states}, "
            f"sealed_unreachable={sealed_unreachable_cells}, score={score:.1f})"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
