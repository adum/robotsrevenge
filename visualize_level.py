#!/usr/bin/env python3
"""
Visualize a SenseJump level in the terminal.

Usage:
  python3 visualize_level.py levels/1.level
  python3 visualize_level.py "v=2&x=11&y=11&board=...,...&sx=5&sy=5&plim=14&elim=420"
  python3 visualize_level.py levels/100.level --svg-out /tmp/level.svg
  python3 visualize_level.py levels/100.level --solution-file solutions/100.solution.json --svg-out /tmp/level_trace.svg
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from xml.sax.saxutils import escape

import sensejump_core as core


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


SVG_BG = "#f7f4e8"
SVG_EMPTY = "#fefcf2"
SVG_BLOCKED = "#353535"
SVG_START = "#ff9f1c"
SVG_PATH = "#4caf50"
SVG_SENSED_BLOCK = "#7c7c7c"
SVG_GRID = "#ded7c3"


def color_block(color_code: int, text: str = "  ") -> str:
    return f"\x1b[48;5;{color_code}m{text}\x1b[0m"


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
                dir=core.NORTH_DIR,
                pc=0,
                jump_exec_count=0,
                sense_exec_count=0,
            ),
            trail_cells,
            sensed_block_cells,
        )

    x = level.start_x
    y = level.start_y
    dir_index = core.NORTH_DIR
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


def render(
    level: core.Level,
    use_color: bool,
    trail_cells: set[tuple[int, int]] | None = None,
    sensed_block_cells: set[tuple[int, int]] | None = None,
) -> str:
    trail = trail_cells or set()
    sensed_blocks = sensed_block_cells or set()
    lines: list[str] = []
    level_name = level.level_id if level.level_id is not None else "?"
    lines.append(
        f"SenseJump level {level_name} ({level.width}x{level.height}) "
        f"start=({level.start_x},{level.start_y},{core.DIR_ORDER[core.NORTH_DIR]}) "
        f"plim={level.program_limit} elim={level.execution_limit}"
    )
    if level.solution_hash:
        lines.append(f"solhash={level.solution_hash}")

    for y in range(level.height):
        row: list[str] = []
        for x in range(level.width):
            is_start = x == level.start_x and y == level.start_y
            is_blocked = level.board[y][x]
            on_trail = (x, y) in trail
            sensed_block = (x, y) in sensed_blocks
            if use_color:
                if is_start:
                    row.append(color_block(COLOR_START, DIR_CHARS[core.NORTH_DIR] + " "))
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
                    row.append(DIR_CHARS[core.NORTH_DIR] + " ")
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


def render_svg(
    level: core.Level,
    cell_size: int,
    trail_cells: set[tuple[int, int]] | None = None,
    sensed_block_cells: set[tuple[int, int]] | None = None,
) -> str:
    trail = trail_cells or set()
    sensed_blocks = sensed_block_cells or set()
    cs = max(1, cell_size)
    margin = max(8, cs)
    board_x = margin
    board_y = margin
    board_w = level.width * cs
    board_h = level.height * cs
    width = board_w + margin * 2
    height = board_h + margin * 2

    lines: list[str] = []
    lines.append('<?xml version="1.0" encoding="UTF-8"?>')
    lines.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img">'
    )
    lines.append(f'<rect x="0" y="0" width="{width}" height="{height}" fill="{SVG_BG}" />')
    lines.append(
        f'<rect x="{board_x}" y="{board_y}" width="{board_w}" height="{board_h}" fill="{SVG_EMPTY}" />'
    )

    # Grid lines stay subtle and are omitted for very dense renders.
    if cs >= 4:
        for gx in range(level.width + 1):
            x = board_x + gx * cs
            lines.append(
                f'<line x1="{x}" y1="{board_y}" x2="{x}" y2="{board_y + board_h}" '
                f'stroke="{SVG_GRID}" stroke-width="1" />'
            )
        for gy in range(level.height + 1):
            y = board_y + gy * cs
            lines.append(
                f'<line x1="{board_x}" y1="{y}" x2="{board_x + board_w}" y2="{y}" '
                f'stroke="{SVG_GRID}" stroke-width="1" />'
            )

    for y in range(level.height):
        for x in range(level.width):
            px = board_x + x * cs
            py = board_y + y * cs
            is_start = x == level.start_x and y == level.start_y
            is_blocked = level.board[y][x]
            on_trail = (x, y) in trail
            sensed_block = (x, y) in sensed_blocks

            fill = SVG_EMPTY
            if is_blocked and sensed_block:
                fill = SVG_SENSED_BLOCK
            elif is_blocked:
                fill = SVG_BLOCKED
            elif on_trail:
                fill = SVG_PATH
            if fill != SVG_EMPTY:
                lines.append(f'<rect x="{px}" y="{py}" width="{cs}" height="{cs}" fill="{fill}" />')

            if is_start:
                lines.append(f'<rect x="{px}" y="{py}" width="{cs}" height="{cs}" fill="{SVG_START}" />')
                arrow = escape(DIR_CHARS[core.NORTH_DIR])
                font_size = max(9, int(cs * 0.9))
                tx = px + cs / 2
                ty = py + cs * 0.75
                lines.append(
                    f'<text x="{tx:.1f}" y="{ty:.1f}" font-size="{font_size}" text-anchor="middle" '
                    f'font-family="monospace" fill="#111">{arrow}</text>'
                )

    lines.append("</svg>")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Visualize a SenseJump level.")
    parser.add_argument("level", help="Level string or path to a .level file.")
    parser.add_argument("--no-color", action="store_true", help="Disable ANSI color output.")
    parser.add_argument(
        "--solution-file",
        type=Path,
        default=None,
        help="Optional program source (.solution.json or plain text file) for trace overlay.",
    )
    parser.add_argument(
        "--svg-out",
        type=Path,
        default=None,
        help="Write SVG visualization to this path (recommended for large levels).",
    )
    parser.add_argument(
        "--cell-size",
        type=int,
        default=6,
        help="Cell size in pixels for --svg-out (default: 6).",
    )
    args = parser.parse_args()

    use_color = sys.stdout.isatty() and not args.no_color
    if os.environ.get("NO_COLOR"):
        use_color = False

    try:
        level_raw = core.read_text_arg(args.level)
        level = core.parse_level(level_raw)
    except (OSError, core.LevelFormatError) as exc:
        print(f"Error: {exc}")
        return 2

    trail_cells: set[tuple[int, int]] | None = None
    sensed_block_cells: set[tuple[int, int]] | None = None
    run_result: core.RunResult | None = None
    meander_metrics: core.SolutionMeanderMetrics | None = None
    if args.solution_file is not None:
        try:
            program_raw = load_solution_program_text(args.solution_file)
            program = core.parse_program_text(program_raw)
            run_result, trail_cells, sensed_block_cells = simulate_with_trace(
                level, program, level.execution_limit
            )
            meander_metrics = core.solution_meander_metrics(level, program, level.execution_limit)
        except (OSError, ValueError, core.ProgramFormatError) as exc:
            print(f"Error: {exc}")
            return 2

    if args.svg_out is not None:
        try:
            svg = render_svg(
                level,
                args.cell_size,
                trail_cells=trail_cells,
                sensed_block_cells=sensed_block_cells,
            )
            args.svg_out.parent.mkdir(parents=True, exist_ok=True)
            args.svg_out.write_text(svg, encoding="utf-8")
        except OSError as exc:
            print(f"Error: {exc}")
            return 2
        print(f"Wrote SVG: {args.svg_out}")
    else:
        print(
            render(
                level,
                use_color,
                trail_cells=trail_cells,
                sensed_block_cells=sensed_block_cells,
            )
        )

    if run_result is not None:
        print(
            f"Trace: outcome={run_result.outcome} steps={run_result.steps} "
            f"jump_exec={run_result.jump_exec_count} sense_exec={run_result.sense_exec_count}"
        )
    if meander_metrics is not None:
        print(
            "Meander: "
            f"score={meander_metrics.score:.2f} "
            f"ineff={meander_metrics.inefficiency_ratio:.3f} "
            f"coverage={meander_metrics.coarse_coverage_ratio:.3f} "
            f"spread={meander_metrics.spread_ratio:.3f} "
            f"turns={meander_metrics.significant_turns}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
