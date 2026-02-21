#!/usr/bin/env python3
from __future__ import annotations

import argparse
import itertools
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import sensejump_core as core


VALID_OPS = set(core.PROGRAM_OPS)
COLOR_BLOCKED = 236
COLOR_EMPTY = 230
COLOR_START = 208
COLOR_PATH = 120
COLOR_SENSED_BLOCK = 244
DIR_CHARS = {
    0: "^",
    1: ">",
    2: "v",
    3: "<",
}


@dataclass
class SearchStats:
    tested_programs: int = 0
    tested_templates: int = 0
    stopped_by_timeout: bool = False
    stopped_by_budget: bool = False
    elapsed_seconds: float = 0.0


def parse_ops(raw_ops: str) -> list[str]:
    ops = [ch.upper() for ch in raw_ops if not ch.isspace()]
    if not ops:
        raise ValueError("Operation set cannot be empty.")
    for op in ops:
        if op not in VALID_OPS:
            raise ValueError(f"Invalid op in --ops: {op!r}. Allowed: {''.join(core.PROGRAM_OPS)}")
    return ops


def jump_offsets_for_length(length: int, max_jump_distance: int, full_jump_range: bool) -> list[int]:
    if length <= 1:
        return [1]
    bound = length - 1 if full_jump_range else min(length - 1, max_jump_distance)
    offsets: list[int] = []
    for distance in range(1, bound + 1):
        offsets.append(-distance)
        offsets.append(distance)
    return offsets


def level_solved(level: core.Level, program: list[core.Instruction]) -> bool:
    result = core.simulate_program(level, program, level.execution_limit)
    return result.outcome == "escape"


def load_solution_program_text(path: Path) -> str:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"Could not read solution file {path}: {exc}") from exc

    stripped = raw.strip()
    if not stripped:
        raise ValueError(f"Solution file {path} is empty.")

    if stripped.startswith("{"):
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Malformed JSON in solution file {path}: {exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"Solution file {path} must contain a JSON object.")
        solution_program = payload.get("solution_program")
        if not isinstance(solution_program, str) or not solution_program.strip():
            raise ValueError(f"Solution JSON file {path} is missing non-empty solution_program.")
        return solution_program

    return stripped


def color_block(color_code: int, text: str = "  ") -> str:
    return f"\x1b[48;5;{color_code}m{text}\x1b[0m"


def simulate_with_trace(
    level: core.Level,
    program: list[core.Instruction],
    max_steps: int | None = None,
) -> tuple[core.RunResult, set[tuple[int, int]], set[tuple[int, int]]]:
    trail_cells: set[tuple[int, int]] = {(level.start_x, level.start_y)}
    sensed_block_cells: set[tuple[int, int]] = set()

    if not program:
        return (
            core.RunResult(
                outcome="invalid",
                steps=0,
                x=level.start_x,
                y=level.start_y,
                dir=level.start_dir,
                pc=0,
                jump_exec_count=0,
                sense_exec_count=0,
            ),
            trail_cells,
            sensed_block_cells,
        )

    x = level.start_x
    y = level.start_y
    dir_index = level.start_dir
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
                    trail_cells,
                    sensed_block_cells,
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
                    trail_cells,
                    sensed_block_cells,
                )
            x = nx
            y = ny
            trail_cells.add((x, y))
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
            if blocked:
                sensed_block_cells.add((nx, ny))
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
            trail_cells,
            sensed_block_cells,
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
        trail_cells,
        sensed_block_cells,
    )


def render_trace_board(
    level: core.Level,
    trail_cells: set[tuple[int, int]],
    sensed_block_cells: set[tuple[int, int]],
    use_color: bool,
) -> str:
    lines: list[str] = []
    for y in range(level.height):
        row: list[str] = []
        for x in range(level.width):
            is_start = x == level.start_x and y == level.start_y
            is_blocked = level.board[y][x]
            on_trail = (x, y) in trail_cells
            sensed_block = (x, y) in sensed_block_cells
            if use_color:
                if is_start:
                    row.append(color_block(COLOR_START, DIR_CHARS[level.start_dir] + " "))
                elif is_blocked and sensed_block:
                    row.append(color_block(COLOR_SENSED_BLOCK))
                elif is_blocked:
                    row.append(color_block(COLOR_BLOCKED))
                elif on_trail:
                    row.append(color_block(COLOR_PATH))
                else:
                    row.append(color_block(COLOR_EMPTY))
            else:
                if is_start:
                    row.append(DIR_CHARS[level.start_dir] + " ")
                elif is_blocked and sensed_block:
                    row.append("!!")
                elif is_blocked:
                    row.append("##")
                elif on_trail:
                    row.append("++")
                else:
                    row.append("..")
        lines.append("".join(row))
    return "\n".join(lines)


def solve_bruteforce(
    level: core.Level,
    min_length: int,
    max_length: int,
    ops: list[str],
    max_jump_distance: int,
    full_jump_range: bool,
    timeout_seconds: float,
    max_programs: int,
    require_sense: bool,
    require_jump: bool,
    verbose: bool,
) -> tuple[list[core.Instruction] | None, SearchStats]:
    start = time.monotonic()
    stats = SearchStats()
    progress_stream = sys.stderr
    single_line_progress = verbose and progress_stream.isatty()
    last_report_time = 0.0
    last_report_programs = 0
    last_report_templates = 0
    last_line_width = 0

    def reached_limits() -> bool:
        now = time.monotonic()
        if timeout_seconds > 0 and (now - start) >= timeout_seconds:
            stats.stopped_by_timeout = True
            return True
        if max_programs > 0 and stats.tested_programs >= max_programs:
            stats.stopped_by_budget = True
            return True
        return False

    def report_progress(current_length: int, force: bool = False) -> None:
        nonlocal last_report_time, last_report_programs, last_report_templates, last_line_width
        if not verbose:
            return
        now = time.monotonic()
        elapsed = max(0.0, now - start)
        if not force:
            template_delta = stats.tested_templates - last_report_templates
            program_delta = stats.tested_programs - last_report_programs
            if template_delta <= 0 and program_delta <= 0:
                return
            if elapsed - last_report_time < 0.2 and template_delta < 25000 and program_delta < 10000:
                return

        rate = stats.tested_programs / elapsed if elapsed > 0 else 0.0
        line = (
            f"[search] len={current_length}/{max_length} tested={stats.tested_programs} "
            f"templates={stats.tested_templates} rate={rate:.0f}/s elapsed={elapsed:.2f}s"
        )
        if single_line_progress:
            padding = ""
            if len(line) < last_line_width:
                padding = " " * (last_line_width - len(line))
            progress_stream.write("\r" + line + padding)
            progress_stream.flush()
            last_line_width = max(last_line_width, len(line))
        else:
            print(line, file=progress_stream)
        last_report_time = elapsed
        last_report_programs = stats.tested_programs
        last_report_templates = stats.tested_templates

    def finish_progress_line() -> None:
        if single_line_progress and last_line_width > 0:
            progress_stream.write("\n")
            progress_stream.flush()

    current_length = min_length
    for length in range(min_length, max_length + 1):
        current_length = length
        if reached_limits():
            break

        jump_offsets = jump_offsets_for_length(length, max_jump_distance, full_jump_range)
        if "J" in ops and not jump_offsets:
            continue

        for template in itertools.product(ops, repeat=length):
            if reached_limits():
                break
            stats.tested_templates += 1
            report_progress(length)

            if "F" not in template:
                continue
            if require_sense and "S" not in template:
                continue
            jump_positions = [index for index, op in enumerate(template) if op == "J"]
            if require_jump and not jump_positions:
                continue

            if not jump_positions:
                program = [core.Instruction(op, 1) for op in template]
                stats.tested_programs += 1
                report_progress(length)
                if level_solved(level, program):
                    report_progress(length, force=True)
                    finish_progress_line()
                    stats.elapsed_seconds = time.monotonic() - start
                    return program, stats
                continue

            for jump_args in itertools.product(jump_offsets, repeat=len(jump_positions)):
                if reached_limits():
                    break
                program: list[core.Instruction] = []
                jump_index = 0
                for op in template:
                    if op == "J":
                        program.append(core.Instruction("J", jump_args[jump_index]))
                        jump_index += 1
                    else:
                        program.append(core.Instruction(op, 1))
                stats.tested_programs += 1
                report_progress(length)
                if level_solved(level, program):
                    report_progress(length, force=True)
                    finish_progress_line()
                    stats.elapsed_seconds = time.monotonic() - start
                    return program, stats

    report_progress(current_length, force=True)
    finish_progress_line()
    stats.elapsed_seconds = time.monotonic() - start
    return None, stats


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Brute force solver for SenseJump levels."
    )
    parser.add_argument(
        "level",
        nargs="?",
        help="Level string or path to a .level file. If omitted, read stdin.",
    )
    parser.add_argument(
        "--min-length",
        type=int,
        default=1,
        help="Minimum program length to test (default: 1).",
    )
    parser.add_argument(
        "--max-length",
        type=int,
        default=None,
        help="Maximum program length to test (default: level plim).",
    )
    parser.add_argument(
        "--ops",
        default="FLRSJ",
        help="Instruction alphabet to search (subset of FLRSJ, default: FLRSJ).",
    )
    parser.add_argument(
        "--max-jump-distance",
        type=int,
        default=3,
        help="Maximum absolute jump distance when limited jump mode is enabled (default: 3).",
    )
    parser.add_argument(
        "--full-jump-range",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Use full jump range per length (1..length-1). "
            "Default: enabled. Use --no-full-jump-range to respect --max-jump-distance."
        ),
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=0.0,
        help="Search timeout in seconds, <=0 means no timeout (default: 0 = no timeout).",
    )
    parser.add_argument(
        "--max-programs",
        type=int,
        default=0,
        help="Maximum candidate programs to simulate, <=0 means unlimited (default: 0 = unlimited).",
    )
    parser.add_argument(
        "--require-sense",
        action="store_true",
        help="Only test programs that contain at least one S instruction.",
    )
    parser.add_argument(
        "--require-jump",
        action="store_true",
        help="Only test programs that contain at least one J instruction.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print live search progress and exit summary to stderr (single-line progress on TTY).",
    )
    parser.add_argument(
        "--solution-file",
        type=Path,
        default=None,
        help=(
            "Optional program source (.solution.json or plain text file). "
            "When provided, skip brute-force search and evaluate only this program."
        ),
    )
    return parser


def main(argv: list[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        level_raw = sys.stdin.read().strip() if args.level is None else core.read_text_arg(args.level)
    except OSError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    if not level_raw:
        print("Error: empty level input", file=sys.stderr)
        return 2

    try:
        level = core.parse_level(level_raw)
    except (core.LevelFormatError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    min_length = max(1, args.min_length)
    max_length = level.program_limit if args.max_length is None else args.max_length
    max_length = min(max_length, level.program_limit)
    if max_length < min_length:
        print("Error: max length must be >= min length after clamping to level limit.", file=sys.stderr)
        return 2

    solution_result: core.RunResult | None = None
    if args.solution_file is not None:
        try:
            solution_raw = load_solution_program_text(args.solution_file)
            candidate_solution = core.parse_program_text(solution_raw)
        except (ValueError, core.ProgramFormatError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 2
        if not candidate_solution:
            print("Error: provided solution is empty.", file=sys.stderr)
            return 2
        if len(candidate_solution) > level.program_limit:
            print(
                f"Error: provided solution length {len(candidate_solution)} exceeds level limit {level.program_limit}.",
                file=sys.stderr,
            )
            return 2

        if args.verbose:
            print(
                "[level] "
                f"id={level.level_id or '?'} "
                f"size={level.width}x{level.height} "
                f"start=({level.start_x},{level.start_y},{core.DIR_ORDER[level.start_dir]}) "
                f"plim={level.program_limit} elim={level.execution_limit} "
                f"mode=provided file={args.solution_file}",
                file=sys.stderr,
            )

        start = time.monotonic()
        solution_result = core.simulate_program(level, candidate_solution, level.execution_limit)
        stats = SearchStats(
            tested_programs=1,
            tested_templates=1,
            elapsed_seconds=time.monotonic() - start,
        )
        solution = candidate_solution if solution_result.outcome == "escape" else None
    else:
        try:
            ops = parse_ops(args.ops)
        except ValueError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 2

        max_jump_distance = max(1, args.max_jump_distance)
        if args.verbose:
            print(
                "[level] "
                f"id={level.level_id or '?'} "
                f"size={level.width}x{level.height} "
                f"start=({level.start_x},{level.start_y},{core.DIR_ORDER[level.start_dir]}) "
                f"plim={level.program_limit} elim={level.execution_limit} "
                f"search_len={min_length}-{max_length} "
                f"ops={''.join(ops)} "
                f"jump={'full' if args.full_jump_range else max_jump_distance} "
                f"max_programs={args.max_programs} "
                f"timeout={'none' if args.timeout <= 0 else f'{args.timeout}s'}",
                file=sys.stderr,
            )
        solution, stats = solve_bruteforce(
            level=level,
            min_length=min_length,
            max_length=max_length,
            ops=ops,
            max_jump_distance=max_jump_distance,
            full_jump_range=args.full_jump_range,
            timeout_seconds=args.timeout,
            max_programs=args.max_programs,
            require_sense=args.require_sense,
            require_jump=args.require_jump,
            verbose=args.verbose,
        )

    if solution is None:
        print("No solution found")
        if args.verbose:
            if args.solution_file is not None and solution_result is not None:
                reason = f"provided solution outcome={solution_result.outcome}"
            else:
                reason = "complete search"
                if stats.stopped_by_timeout:
                    reason = "timeout"
                elif stats.stopped_by_budget:
                    reason = "program budget"
            print(
                f"[done] reason={reason} tested={stats.tested_programs} "
                f"templates={stats.tested_templates} elapsed={stats.elapsed_seconds:.2f}s",
                file=sys.stderr,
            )
        return 1

    print(core.format_program(solution))
    if args.verbose:
        use_color = sys.stderr.isatty() and not os.environ.get("NO_COLOR")
        run_result, trail_cells, sensed_block_cells = simulate_with_trace(
            level,
            solution,
            level.execution_limit,
        )
        print(
            f"[trace] outcome={run_result.outcome} steps={run_result.steps} "
            f"jump_exec={run_result.jump_exec_count} sense_exec={run_result.sense_exec_count}",
            file=sys.stderr,
        )
        print(
            "[trace] legend: start=orange, path=green, sensed blocked=gray, blocked=dark",
            file=sys.stderr,
        )
        print(render_trace_board(level, trail_cells, sensed_block_cells, use_color), file=sys.stderr)
        print(
            f"[done] solved tested={stats.tested_programs} templates={stats.tested_templates} "
            f"elapsed={stats.elapsed_seconds:.2f}s",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
