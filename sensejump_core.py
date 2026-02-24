#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import math
import random
import re
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qsl

MAX_PROGRAM_LIMIT = 128
PROGRAM_OPS = ("F", "L", "R", "S", "J")
DIR_ORDER = ("N", "E", "S", "W")
DIR_TO_INDEX = {name: index for index, name in enumerate(DIR_ORDER)}
DIR_DELTAS = ((0, -1), (1, 0), (0, 1), (-1, 0))
NORTH_DIR = DIR_TO_INDEX["N"]
TOKEN_SPLIT_RE = re.compile(r"[\s,;]+")
INT_TOKEN_RE = re.compile(r"^[+-]?\d+$")


@dataclass
class Instruction:
    op: str
    arg: int = 1


@dataclass
class Level:
    version: int
    level_id: str | None
    width: int
    height: int
    board: list[list[bool]]  # board[y][x] == True means blocked
    start_x: int
    start_y: int
    start_dir: int
    program_limit: int
    execution_limit: int
    solution_hash: str | None = None


@dataclass
class RunResult:
    outcome: str  # escape, crash, timeout, invalid
    steps: int
    x: int
    y: int
    dir: int
    pc: int
    jump_exec_count: int
    sense_exec_count: int


@dataclass(frozen=True)
class GenerateOptions:
    width: int = 11
    height: int = 11
    density: float = 0.28
    solution_length: int = 9
    program_limit: int = 14
    execution_limit: int = 420
    max_attempts: int = 650
    max_straight_run: int = 10
    min_direction_types_to_exit: int = 3
    min_steps_size_factor: float = 0.6


@dataclass
class GeneratedLevel:
    level: Level
    level_text: str
    level_hash: str
    solution: list[Instruction]
    solution_text: str
    solution_steps: int
    min_moves_to_exit: int
    min_direction_types_to_exit: int
    attempts_used: int


class LevelFormatError(ValueError):
    pass


class ProgramFormatError(ValueError):
    pass


def read_text_arg(value: str) -> str:
    path = Path(value)
    if path.is_file():
        return path.read_text(encoding="utf-8").strip()
    return value.strip()


def wrap(value: int, mod: int) -> int:
    if mod <= 0:
        return 0
    normalized = value % mod
    return normalized + mod if normalized < 0 else normalized


def _effective_jump_offset(offset: int, program_length: int) -> int:
    if offset == 0:
        offset = 1
    return wrap(offset, program_length)


def in_bounds(x: int, y: int, width: int, height: int) -> bool:
    return 0 <= x < width and 0 <= y < height


def _parse_int_param(params: dict[str, str], key: str, default: int | None = None) -> int:
    raw = params.get(key)
    if raw is None or raw == "":
        if default is None:
            raise LevelFormatError(f"Missing required parameter: {key}")
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise LevelFormatError(f"Invalid integer for {key}: {raw!r}") from exc


def parse_level(level_raw: str) -> Level:
    cleaned = level_raw.strip().strip("\"'")
    params = dict(parse_qsl(cleaned.lstrip("?#"), keep_blank_values=True))
    if not params:
        raise LevelFormatError("Level string is empty or malformed.")

    width = _parse_int_param(params, "x")
    height = _parse_int_param(params, "y")
    if width <= 0 or height <= 0:
        raise LevelFormatError("x and y must be positive integers.")

    board_raw = params.get("board")
    if not board_raw:
        raise LevelFormatError("Missing required parameter: board")

    board_flat = "".join(ch for ch in board_raw if ch not in ", \n\r\t")
    expected_len = width * height
    if len(board_flat) != expected_len:
        raise LevelFormatError(
            f"Board length {len(board_flat)} does not match x*y ({expected_len})."
        )

    board: list[list[bool]] = [[False for _ in range(width)] for _ in range(height)]
    for index, ch in enumerate(board_flat):
        x = index % width
        y = index // width
        if ch in ("X", "x", "#"):
            board[y][x] = True
        elif ch == ".":
            board[y][x] = False
        else:
            raise LevelFormatError(f"Invalid board character {ch!r} at index {index}.")

    start_x = _parse_int_param(params, "sx", width // 2)
    start_y = _parse_int_param(params, "sy", height // 2)
    if not in_bounds(start_x, start_y, width, height):
        raise LevelFormatError("Start coordinates sx, sy are out of bounds.")

    if "sd" in params:
        raise LevelFormatError("Parameter sd is no longer supported; start direction is always North.")
    start_dir = NORTH_DIR

    program_limit = _parse_int_param(params, "plim", 14)
    execution_limit = _parse_int_param(params, "elim", 420)
    if program_limit <= 0:
        raise LevelFormatError("plim must be a positive integer.")
    if execution_limit <= 0:
        raise LevelFormatError("elim must be a positive integer.")

    level_id = params.get("id") or params.get("level") or None
    solution_hash = params.get("solhash") or None

    if board[start_y][start_x]:
        raise LevelFormatError("Start cell cannot be blocked.")

    version = _parse_int_param(params, "v", 2)
    return Level(
        version=version,
        level_id=level_id,
        width=width,
        height=height,
        board=board,
        start_x=start_x,
        start_y=start_y,
        start_dir=start_dir,
        program_limit=program_limit,
        execution_limit=execution_limit,
        solution_hash=solution_hash,
    )


def board_rows(board: list[list[bool]]) -> list[str]:
    if not board:
        return []
    height = len(board)
    width = len(board[0])
    rows: list[str] = []
    for y in range(height):
        rows.append("".join("X" if board[y][x] else "." for x in range(width)))
    return rows


def format_level(level: Level) -> str:
    parts: list[str] = []
    parts.append(f"v={level.version}")
    if level.level_id is not None:
        parts.append(f"id={level.level_id}")
    parts.append(f"x={level.width}")
    parts.append(f"y={level.height}")
    parts.append(f"board={','.join(board_rows(level.board))}")
    parts.append(f"sx={level.start_x}")
    parts.append(f"sy={level.start_y}")
    parts.append(f"plim={level.program_limit}")
    parts.append(f"elim={level.execution_limit}")
    if level.solution_hash:
        parts.append(f"solhash={level.solution_hash}")
    return "&".join(parts)


def parse_program_text(program_raw: str) -> list[Instruction]:
    cleaned = program_raw.strip()
    if not cleaned:
        return []

    raw_tokens = [token for token in TOKEN_SPLIT_RE.split(cleaned) if token]
    program: list[Instruction] = []

    index = 0
    while index < len(raw_tokens):
        token = raw_tokens[index]
        upper = token.upper()

        if token == "↑" or upper == "F":
            program.append(Instruction("F", 1))
            index += 1
            continue
        if token == "↺" or upper == "L":
            program.append(Instruction("L", 1))
            index += 1
            continue
        if token == "↻" or upper == "R":
            program.append(Instruction("R", 1))
            index += 1
            continue
        if upper == "S":
            program.append(Instruction("S", 1))
            index += 1
            continue

        if upper == "J" or upper.startswith("J"):
            suffix = token[1:] if len(token) > 1 else ""
            if suffix == "" and index + 1 < len(raw_tokens) and INT_TOKEN_RE.match(raw_tokens[index + 1]):
                suffix = raw_tokens[index + 1]
                index += 1
            if suffix == "":
                offset = 1
            else:
                if not INT_TOKEN_RE.match(suffix):
                    raise ProgramFormatError(f"Invalid jump offset token: {token!r}")
                offset = int(suffix)
                if offset == 0:
                    offset = 1
            program.append(Instruction("J", offset))
            index += 1
            continue

        raise ProgramFormatError(f"Invalid instruction token: {token!r}")

    return program


def format_program(program: list[Instruction]) -> str:
    tokens: list[str] = []
    for inst in program:
        op = inst.op.upper()
        if op not in PROGRAM_OPS:
            raise ProgramFormatError(f"Unsupported instruction op: {inst.op!r}")
        if op == "J":
            offset = inst.arg if isinstance(inst.arg, int) else 1
            if offset == 0:
                offset = 1
            tokens.append(f"J{offset:+d}")
        else:
            tokens.append(op)
    return " ".join(tokens)


def hash_text(text: str) -> str:
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def compute_program_hash(program: list[Instruction]) -> str:
    return hash_text(format_program(program))


def compute_level_hash(level: Level) -> str:
    return hash_text(format_level(level))


def simulate_program(level: Level, program: list[Instruction], max_steps: int | None = None) -> RunResult:
    if not program:
        return RunResult(
            outcome="invalid",
            steps=0,
            x=level.start_x,
            y=level.start_y,
            dir=NORTH_DIR,
            pc=0,
            jump_exec_count=0,
            sense_exec_count=0,
        )

    x = level.start_x
    y = level.start_y
    dir_index = NORTH_DIR
    pc = 0
    steps = 0
    jump_exec_count = 0
    sense_exec_count = 0
    n = len(program)
    limit = level.execution_limit if max_steps is None else max_steps
    if limit <= 0:
        limit = 1

    while steps < limit:
        inst = program[pc]
        op = inst.op.upper()
        if op == "F":
            dx, dy = DIR_DELTAS[dir_index]
            nx = x + dx
            ny = y + dy
            steps += 1
            if not in_bounds(nx, ny, level.width, level.height):
                return RunResult(
                    outcome="escape",
                    steps=steps,
                    x=nx,
                    y=ny,
                    dir=dir_index,
                    pc=wrap(pc + 1, n),
                    jump_exec_count=jump_exec_count,
                    sense_exec_count=sense_exec_count,
                )
            if level.board[ny][nx]:
                return RunResult(
                    outcome="crash",
                    steps=steps,
                    x=nx,
                    y=ny,
                    dir=dir_index,
                    pc=pc,
                    jump_exec_count=jump_exec_count,
                    sense_exec_count=sense_exec_count,
                )
            x = nx
            y = ny
            pc = wrap(pc + 1, n)
            continue

        if op == "L":
            dir_index = wrap(dir_index - 1, 4)
            pc = wrap(pc + 1, n)
            steps += 1
            continue

        if op == "R":
            dir_index = wrap(dir_index + 1, 4)
            pc = wrap(pc + 1, n)
            steps += 1
            continue

        if op == "S":
            dx, dy = DIR_DELTAS[dir_index]
            nx = x + dx
            ny = y + dy
            blocked = in_bounds(nx, ny, level.width, level.height) and level.board[ny][nx]
            pc = wrap(pc + (1 if blocked else 2), n)
            steps += 1
            sense_exec_count += 1
            continue

        if op == "J":
            offset = inst.arg if isinstance(inst.arg, int) else 1
            if offset == 0:
                offset = 1
            pc = wrap(pc + offset, n)
            steps += 1
            jump_exec_count += 1
            continue

        return RunResult(
            outcome="invalid",
            steps=steps,
            x=x,
            y=y,
            dir=dir_index,
            pc=pc,
            jump_exec_count=jump_exec_count,
            sense_exec_count=sense_exec_count,
        )

    return RunResult(
        outcome="timeout",
        steps=steps,
        x=x,
        y=y,
        dir=dir_index,
        pc=pc,
        jump_exec_count=jump_exec_count,
        sense_exec_count=sense_exec_count,
    )


def verify_program(level: Level, program_raw: str) -> tuple[bool, str, list[Instruction] | None, RunResult | None]:
    try:
        program = parse_program_text(program_raw)
    except ProgramFormatError as exc:
        return False, f"Invalid program: {exc}", None, None

    if not program:
        return False, "Program is empty.", None, None
    if len(program) > level.program_limit:
        return (
            False,
            f"Program length {len(program)} exceeds level limit {level.program_limit}.",
            program,
            None,
        )

    result = simulate_program(level, program, level.execution_limit)
    if result.outcome == "escape":
        return True, f"Solved in {result.steps} steps.", program, result
    if result.outcome == "crash":
        return False, f"Crashed at {result.x}, {result.y} on step {result.steps}.", program, result
    if result.outcome == "timeout":
        return False, f"Execution limit reached at step {result.steps}.", program, result
    return False, "Invalid execution state.", program, result


@dataclass
class _ConstraintTrace:
    requirements: dict[tuple[int, int], bool]
    steps: int
    sense_exec_count: int
    jump_exec_count: int
    sense_true: int
    sense_false: int


def _random_program(length: int, rng: random.Random) -> list[Instruction]:
    if length <= 0:
        return []

    max_jump_distance = min(5, max(1, length - 1))
    jump_offsets: list[int] = []
    for distance in range(1, max_jump_distance + 1):
        for offset in (-distance, distance):
            effective_offset = _effective_jump_offset(offset, length)
            if effective_offset in (0, 1):
                continue
            jump_offsets.append(offset)
    if not jump_offsets:
        jump_offsets = [1]

    program: list[Instruction] = []
    for _ in range(length):
        roll = rng.random()
        if roll < 0.34:
            program.append(Instruction("F", 1))
        elif roll < 0.52:
            program.append(Instruction("L", 1))
        elif roll < 0.70:
            program.append(Instruction("R", 1))
        elif roll < 0.86:
            program.append(Instruction("S", 1))
        else:
            offset = jump_offsets[rng.randint(0, len(jump_offsets) - 1)]
            program.append(Instruction("J", offset))

    if not any(inst.op == "S" for inst in program):
        program[rng.randint(0, length - 1)] = Instruction("S", 1)
    if not any(inst.op == "J" for inst in program):
        offset = jump_offsets[rng.randint(0, len(jump_offsets) - 1)]
        program[rng.randint(0, length - 1)] = Instruction("J", offset)

    forward_count = sum(1 for inst in program if inst.op == "F")
    if forward_count < 2:
        first = rng.randint(0, length - 1)
        second = rng.randint(0, length - 1)
        program[first] = Instruction("F", 1)
        program[second] = Instruction("F", 1)
    return program


def _build_constraint_trace(
    program: list[Instruction],
    start_x: int,
    start_y: int,
    width: int,
    height: int,
    max_steps: int,
    rng: random.Random,
) -> _ConstraintTrace | None:
    requirements: dict[tuple[int, int], bool] = {(start_x, start_y): False}
    state_visits: dict[tuple[int, int, int, int], int] = {}

    x = start_x
    y = start_y
    dir_index = NORTH_DIR
    pc = 0
    steps = 0
    sense_exec_count = 0
    jump_exec_count = 0
    sense_true = 0
    sense_false = 0
    n = len(program)

    while steps < max_steps:
        visit_key = (x, y, dir_index, pc)
        visit_count = state_visits.get(visit_key, 0) + 1
        state_visits[visit_key] = visit_count
        if visit_count > 10:
            return None

        inst = program[pc]
        op = inst.op
        if op == "F":
            dx, dy = DIR_DELTAS[dir_index]
            nx = x + dx
            ny = y + dy
            steps += 1
            if not in_bounds(nx, ny, width, height):
                return _ConstraintTrace(
                    requirements=requirements,
                    steps=steps,
                    sense_exec_count=sense_exec_count,
                    jump_exec_count=jump_exec_count,
                    sense_true=sense_true,
                    sense_false=sense_false,
                )
            forced = requirements.get((nx, ny))
            if forced is True:
                return None
            requirements[(nx, ny)] = False
            x = nx
            y = ny
            pc = wrap(pc + 1, n)
            continue

        if op == "L":
            dir_index = wrap(dir_index - 1, 4)
            pc = wrap(pc + 1, n)
            steps += 1
            continue

        if op == "R":
            dir_index = wrap(dir_index + 1, 4)
            pc = wrap(pc + 1, n)
            steps += 1
            continue

        if op == "S":
            dx, dy = DIR_DELTAS[dir_index]
            nx = x + dx
            ny = y + dy
            steps += 1
            sense_exec_count += 1
            if not in_bounds(nx, ny, width, height):
                sense_false += 1
                pc = wrap(pc + 2, n)
                continue

            key = (nx, ny)
            forced = requirements.get(key)
            if forced is None:
                dist_to_edge = min(nx, width - 1 - nx, ny, height - 1 - ny)
                chance_blocked = 0.2 if dist_to_edge <= 1 else 0.42
                blocked = rng.random() < chance_blocked
                requirements[key] = blocked
            else:
                blocked = forced

            if blocked:
                sense_true += 1
                pc = wrap(pc + 1, n)
            else:
                sense_false += 1
                pc = wrap(pc + 2, n)
            continue

        if op == "J":
            offset = inst.arg if isinstance(inst.arg, int) else 1
            if offset == 0:
                offset = 1
            pc = wrap(pc + offset, n)
            jump_exec_count += 1
            steps += 1
            continue

        pc = wrap(pc + 1, n)
        steps += 1

    return None


def _materialize_board(
    width: int,
    height: int,
    density: float,
    requirements: dict[tuple[int, int], bool],
    start_x: int,
    start_y: int,
    rng: random.Random,
) -> list[list[bool]]:
    board: list[list[bool]] = []
    for _ in range(height):
        board.append([rng.random() < density for _ in range(width)])

    for (x, y), blocked in requirements.items():
        if in_bounds(x, y, width, height):
            board[y][x] = bool(blocked)

    board[start_y][start_x] = False
    return board


def has_straight_escape_lane_from_start(board: list[list[bool]], start_x: int, start_y: int) -> bool:
    height = len(board)
    width = len(board[0]) if height else 0
    for dir_index in range(4):
        dx, dy = DIR_DELTAS[dir_index]
        x = start_x
        y = start_y
        while True:
            x += dx
            y += dy
            if not in_bounds(x, y, width, height):
                return True
            if board[y][x]:
                break
    return False


def has_one_turn_escape_path_from_start(board: list[list[bool]], start_x: int, start_y: int) -> bool:
    """
    Returns True if there is an axis-aligned escape path with at most one turn.
    This catches easy L-shaped solutions like "right a bit, then down to exit".
    """
    height = len(board)
    width = len(board[0]) if height else 0
    if width == 0 or height == 0:
        return False

    def clear_horizontal(y: int, x0: int, x1: int) -> bool:
        if x0 == x1:
            return True
        step = 1 if x1 > x0 else -1
        x = x0 + step
        while True:
            if board[y][x]:
                return False
            if x == x1:
                return True
            x += step

    def clear_vertical(x: int, y0: int, y1: int) -> bool:
        if y0 == y1:
            return True
        step = 1 if y1 > y0 else -1
        y = y0 + step
        while True:
            if board[y][x]:
                return False
            if y == y1:
                return True
            y += step

    def clear_to_edge_vertical(x: int, y: int, step: int) -> bool:
        ny = y + step
        while 0 <= ny < height:
            if board[ny][x]:
                return False
            ny += step
        return True

    def clear_to_edge_horizontal(x: int, y: int, step: int) -> bool:
        nx = x + step
        while 0 <= nx < width:
            if board[y][nx]:
                return False
            nx += step
        return True

    # Horizontal first, then vertical to edge.
    for x in range(width):
        if x == start_x:
            continue
        if not clear_horizontal(start_y, start_x, x):
            continue
        if clear_to_edge_vertical(x, start_y, -1) or clear_to_edge_vertical(x, start_y, 1):
            return True

    # Vertical first, then horizontal to edge.
    for y in range(height):
        if y == start_y:
            continue
        if not clear_vertical(start_x, start_y, y):
            continue
        if clear_to_edge_horizontal(start_x, y, -1) or clear_to_edge_horizontal(start_x, y, 1):
            return True

    return False


def block_count(board: list[list[bool]]) -> int:
    return sum(1 for row in board for cell in row if cell)


def seal_unreachable_cells(level: Level) -> int:
    """
    Marks all open cells that are unreachable from the level start as blocked.
    Returns the number of cells that were newly blocked.
    """
    width = level.width
    height = level.height
    start_x = level.start_x
    start_y = level.start_y

    if not in_bounds(start_x, start_y, width, height):
        return 0
    if level.board[start_y][start_x]:
        return 0

    reachable = [[False for _ in range(width)] for _ in range(height)]
    queue: deque[tuple[int, int]] = deque()
    queue.append((start_x, start_y))
    reachable[start_y][start_x] = True

    while queue:
        x, y = queue.popleft()
        for dx, dy in DIR_DELTAS:
            nx = x + dx
            ny = y + dy
            if not in_bounds(nx, ny, width, height):
                continue
            if reachable[ny][nx] or level.board[ny][nx]:
                continue
            reachable[ny][nx] = True
            queue.append((nx, ny))

    sealed_count = 0
    for y in range(height):
        for x in range(width):
            if level.board[y][x]:
                continue
            if reachable[y][x]:
                continue
            level.board[y][x] = True
            sealed_count += 1

    return sealed_count


def minimum_moves_to_exit(level: Level) -> int | None:
    """
    Shortest movement-only path to exit the board from the start cell.
    Counts only orthogonal moves through open cells; turning/sensing/jumps are ignored.
    """
    width = level.width
    height = level.height
    start_x = level.start_x
    start_y = level.start_y

    if not in_bounds(start_x, start_y, width, height):
        return None
    if level.board[start_y][start_x]:
        return None

    visited = [[False for _ in range(width)] for _ in range(height)]
    queue: deque[tuple[int, int, int]] = deque()
    queue.append((start_x, start_y, 0))
    visited[start_y][start_x] = True

    while queue:
        x, y, steps = queue.popleft()
        if x == 0 or y == 0 or x == width - 1 or y == height - 1:
            return steps + 1

        for dx, dy in DIR_DELTAS:
            nx = x + dx
            ny = y + dy
            if not in_bounds(nx, ny, width, height):
                continue
            if visited[ny][nx] or level.board[ny][nx]:
                continue
            visited[ny][nx] = True
            queue.append((nx, ny, steps + 1))

    return None


def _can_escape_with_direction_mask(level: Level, direction_mask: int) -> bool:
    width = level.width
    height = level.height
    start_x = level.start_x
    start_y = level.start_y

    if not in_bounds(start_x, start_y, width, height):
        return False
    if level.board[start_y][start_x]:
        return False

    visited = [[False for _ in range(width)] for _ in range(height)]
    queue: deque[tuple[int, int]] = deque()
    queue.append((start_x, start_y))
    visited[start_y][start_x] = True

    while queue:
        x, y = queue.popleft()
        for dir_index, (dx, dy) in enumerate(DIR_DELTAS):
            if (direction_mask & (1 << dir_index)) == 0:
                continue

            nx = x + dx
            ny = y + dy
            if not in_bounds(nx, ny, width, height):
                return True
            if visited[ny][nx] or level.board[ny][nx]:
                continue
            visited[ny][nx] = True
            queue.append((nx, ny))

    return False


def minimum_distinct_directions_to_exit(level: Level) -> int | None:
    """
    Smallest number of distinct movement directions needed to leave the board.
    Counts among N/E/S/W only and ignores turning/sensing/jumps.
    """
    width = level.width
    height = level.height
    start_x = level.start_x
    start_y = level.start_y
    if not in_bounds(start_x, start_y, width, height):
        return None
    if level.board[start_y][start_x]:
        return None

    masks = list(range(1, 1 << len(DIR_DELTAS)))
    masks.sort(key=lambda mask: (mask.bit_count(), mask))
    for direction_mask in masks:
        if _can_escape_with_direction_mask(level, direction_mask):
            return direction_mask.bit_count()
    return None


def has_meaningless_jump_instruction(program: list[Instruction]) -> bool:
    """
    Returns True when program contains jumps that do not change control flow:
    - effective jump of +1 (same as normal fall-through)
    - effective jump of +0 (self-jump)
    """
    n = len(program)
    if n <= 0:
        return False

    for inst in program:
        if inst.op != "J":
            continue
        offset = inst.arg if isinstance(inst.arg, int) else 1
        effective_offset = _effective_jump_offset(offset, n)
        if effective_offset in (0, 1):
            return True
    return False


def analyze_execution_path(
    level: Level,
    program: list[Instruction],
    max_steps: int,
) -> tuple[bool, bool]:
    """
    Returns (has_immediate_turn_cancel, has_dead_instruction) for the executed path.
    """
    n = len(program)
    if n <= 0:
        return False, False

    x = level.start_x
    y = level.start_y
    dir_index = NORTH_DIR
    pc = 0
    steps = 0
    limit = max_steps if max_steps > 0 else 1
    seen_pc = [False for _ in range(n)]
    seen_count = 0
    previous_turn: str | None = None
    has_turn_cancel = False

    while steps < limit:
        if not seen_pc[pc]:
            seen_pc[pc] = True
            seen_count += 1

        inst = program[pc]
        op = inst.op.upper()

        if op == "F":
            previous_turn = None
            dx, dy = DIR_DELTAS[dir_index]
            nx = x + dx
            ny = y + dy
            steps += 1
            if not in_bounds(nx, ny, level.width, level.height):
                break
            if level.board[ny][nx]:
                break
            x = nx
            y = ny
            pc = wrap(pc + 1, n)
            continue

        if op == "L":
            if previous_turn == "R":
                has_turn_cancel = True
            previous_turn = "L"
            dir_index = wrap(dir_index - 1, 4)
            pc = wrap(pc + 1, n)
            steps += 1
            continue

        if op == "R":
            if previous_turn == "L":
                has_turn_cancel = True
            previous_turn = "R"
            dir_index = wrap(dir_index + 1, 4)
            pc = wrap(pc + 1, n)
            steps += 1
            continue

        if op == "S":
            previous_turn = None
            dx, dy = DIR_DELTAS[dir_index]
            nx = x + dx
            ny = y + dy
            blocked = in_bounds(nx, ny, level.width, level.height) and level.board[ny][nx]
            pc = wrap(pc + (1 if blocked else 2), n)
            steps += 1
            continue

        if op == "J":
            previous_turn = None
            offset = inst.arg if isinstance(inst.arg, int) else 1
            if offset == 0:
                offset = 1
            pc = wrap(pc + offset, n)
            steps += 1
            continue

        break

    return has_turn_cancel, seen_count < n


def has_straight_run_at_least(
    level: Level,
    program: list[Instruction],
    run_limit: int,
    max_steps: int,
) -> bool:
    """
    Returns True if replaying program yields at least run_limit consecutive forward
    moves in the same absolute direction.
    """
    if run_limit <= 0 or not program:
        return False

    x = level.start_x
    y = level.start_y
    dir_index = NORTH_DIR
    pc = 0
    steps = 0
    n = len(program)
    last_move_dir = -1
    straight_run = 0

    while steps < max_steps:
        inst = program[pc]
        op = inst.op

        if op == "F":
            if dir_index == last_move_dir:
                straight_run += 1
            else:
                last_move_dir = dir_index
                straight_run = 1
            if straight_run >= run_limit:
                return True

            dx, dy = DIR_DELTAS[dir_index]
            nx = x + dx
            ny = y + dy
            steps += 1
            if not in_bounds(nx, ny, level.width, level.height):
                return False
            if level.board[ny][nx]:
                return False
            x = nx
            y = ny
            pc = wrap(pc + 1, n)
            continue

        if op == "L":
            dir_index = wrap(dir_index - 1, 4)
            pc = wrap(pc + 1, n)
            steps += 1
            continue

        if op == "R":
            dir_index = wrap(dir_index + 1, 4)
            pc = wrap(pc + 1, n)
            steps += 1
            continue

        if op == "S":
            dx, dy = DIR_DELTAS[dir_index]
            nx = x + dx
            ny = y + dy
            blocked = in_bounds(nx, ny, level.width, level.height) and level.board[ny][nx]
            pc = wrap(pc + (1 if blocked else 2), n)
            steps += 1
            continue

        if op == "J":
            offset = inst.arg if isinstance(inst.arg, int) else 1
            if offset == 0:
                offset = 1
            pc = wrap(pc + offset, n)
            steps += 1
            continue

        return False

    return False


def has_easy_two_direction_program(level: Level) -> bool:
    """
    Detects very simple "right-angle staircase" programs of the form:
    [optional turn(s)] F^a T F^b T_opposite, where T is L or R.
    These programs use only two absolute movement directions.
    """
    # Scale segment search with board/program size so large boards do not slip
    # through due to small fixed segment caps.
    max_seg = min(
        max(level.width, level.height),
        max(3, level.program_limit - 2),
    )
    turn_pairs = (("L", "R"), ("R", "L"))
    prefix_options = ((), ("L",), ("R",), ("L", "L"))

    for prefix in prefix_options:
        prefix_len = len(prefix)
        for turn_1, turn_2 in turn_pairs:
            seg_budget = level.program_limit - prefix_len - 2
            if seg_budget < 2:
                continue
            max_seg_a = min(max_seg, seg_budget - 1)
            for seg_a in range(1, max_seg_a + 1):
                max_seg_b = min(max_seg, seg_budget - seg_a)
                for seg_b in range(1, max_seg_b + 1):
                    program: list[Instruction] = []
                    for op in prefix:
                        program.append(Instruction(op, 1))
                    for _ in range(seg_a):
                        program.append(Instruction("F", 1))
                    program.append(Instruction(turn_1, 1))
                    for _ in range(seg_b):
                        program.append(Instruction("F", 1))
                    program.append(Instruction(turn_2, 1))

                    result = simulate_program(level, program, level.execution_limit)
                    if result.outcome == "escape":
                        return True

    return False


def _try_generate_level(
    level_id: str | int | None,
    options: GenerateOptions,
    rng: random.Random,
) -> tuple[tuple[Level, list[Instruction], int] | None, str]:
    start_x = options.width // 2
    start_y = options.height // 2
    hidden_solution = _random_program(options.solution_length, rng)
    if has_meaningless_jump_instruction(hidden_solution):
        return None, "mj"

    trace = _build_constraint_trace(
        hidden_solution,
        start_x,
        start_y,
        options.width,
        options.height,
        options.execution_limit,
        rng,
    )
    if trace is None:
        return None, "ct"

    min_interesting_steps = max(
        10,
        math.floor((options.width + options.height) * options.min_steps_size_factor),
    )
    if trace.steps < min_interesting_steps:
        return None, "ms"
    if trace.jump_exec_count == 0 or trace.sense_exec_count == 0:
        return None, "js"
    if trace.sense_true == 0 or trace.sense_false == 0:
        return None, "sb"

    board = _materialize_board(
        options.width,
        options.height,
        options.density,
        trace.requirements,
        start_x,
        start_y,
        rng,
    )
    if has_straight_escape_lane_from_start(board, start_x, start_y):
        return None, "se"
    if has_one_turn_escape_path_from_start(board, start_x, start_y):
        return None, "ot"

    level = Level(
        version=2,
        level_id=None if level_id is None else str(level_id),
        width=options.width,
        height=options.height,
        board=board,
        start_x=start_x,
        start_y=start_y,
        start_dir=NORTH_DIR,
        program_limit=options.program_limit,
        execution_limit=options.execution_limit,
        solution_hash=None,
    )

    result = simulate_program(level, hidden_solution, options.execution_limit)
    if result.outcome != "escape":
        return None, "ne"
    if result.jump_exec_count == 0 or result.sense_exec_count == 0:
        return None, "rj"
    if result.steps < min_interesting_steps:
        return None, "rs"
    has_turn_cancel, has_dead_instruction = analyze_execution_path(
        level, hidden_solution, options.execution_limit
    )
    if has_turn_cancel:
        return None, "tc"
    if has_dead_instruction:
        return None, "ux"
    if has_straight_run_at_least(level, hidden_solution, options.max_straight_run, options.execution_limit):
        return None, "sr"

    min_direction_types_to_exit = minimum_distinct_directions_to_exit(level)
    if min_direction_types_to_exit is None:
        return None, "np"
    if min_direction_types_to_exit < options.min_direction_types_to_exit:
        return None, "md"

    blocked = block_count(board)
    ratio = blocked / (options.width * options.height)
    if ratio < 0.08 or ratio > 0.70:
        return None, "dn"
    if has_easy_two_direction_program(level):
        return None, "pl"

    return (level, hidden_solution, result.steps), "ok"


def generate_level(
    level_id: str | int | None,
    options: GenerateOptions,
    rng: random.Random | None = None,
    progress_callback: Callable[[int, int, str], None] | None = None,
) -> GeneratedLevel:
    if options.width < 2 or options.height < 2:
        raise ValueError("width and height must be >= 2")
    if not (0.0 <= options.density <= 1.0):
        raise ValueError("density must be between 0 and 1")
    if options.solution_length < 1:
        raise ValueError("solution_length must be >= 1")
    if options.program_limit < 1:
        raise ValueError("program_limit must be >= 1")
    if options.solution_length > options.program_limit:
        raise ValueError("solution_length cannot exceed program_limit")
    if options.program_limit > MAX_PROGRAM_LIMIT:
        raise ValueError(f"program_limit cannot exceed {MAX_PROGRAM_LIMIT}")
    if options.execution_limit < 1:
        raise ValueError("execution_limit must be >= 1")
    if options.max_attempts < 0:
        raise ValueError("max_attempts must be >= 0")
    if options.max_straight_run < 0:
        raise ValueError("max_straight_run must be >= 0")
    if options.min_direction_types_to_exit < 1 or options.min_direction_types_to_exit > 4:
        raise ValueError("min_direction_types_to_exit must be between 1 and 4")
    if not math.isfinite(options.min_steps_size_factor) or options.min_steps_size_factor < 0:
        raise ValueError("min_steps_size_factor must be a finite number >= 0")

    random_source = rng or random.Random()
    attempt = 0
    while options.max_attempts == 0 or attempt < options.max_attempts:
        attempt += 1
        generated, reject_code = _try_generate_level(level_id, options, random_source)
        if progress_callback is not None:
            progress_callback(
                attempt,
                options.max_attempts,
                "accepted" if generated is not None else f"rejected:{reject_code}",
            )
        if generated is None:
            continue

        base_level, solution, solution_steps = generated
        solution_hash = compute_program_hash(solution)
        level = Level(
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
        level_text = format_level(level)
        level_hash = compute_level_hash(level)
        solution_text = format_program(solution)
        min_moves_to_exit = minimum_moves_to_exit(level)
        if min_moves_to_exit is None:
            raise RuntimeError("Generated level has no movement-only path to exit.")
        min_direction_types_to_exit = minimum_distinct_directions_to_exit(level)
        if min_direction_types_to_exit is None:
            raise RuntimeError("Generated level has no direction-subset movement path to exit.")
        return GeneratedLevel(
            level=level,
            level_text=level_text,
            level_hash=level_hash,
            solution=solution,
            solution_text=solution_text,
            solution_steps=solution_steps,
            min_moves_to_exit=min_moves_to_exit,
            min_direction_types_to_exit=min_direction_types_to_exit,
            attempts_used=attempt,
        )

    raise RuntimeError(
        "Generator could not find a valid level. "
        "Try lower density, lower solution length, or higher execution limit."
    )
