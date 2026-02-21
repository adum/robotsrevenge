#!/usr/bin/env python3
"""
Visualize a SenseJump level in the terminal.

Usage:
  python3 visualize_level.py levels/1.level
  python3 visualize_level.py "v=2&x=11&y=11&board=...,...&sx=5&sy=5&plim=14&elim=420"
"""

from __future__ import annotations

import argparse
import os
import sys

import sensejump_core as core


COLOR_BLOCKED = 236
COLOR_EMPTY = 230
COLOR_START = 208


DIR_CHARS = {
    0: "^",
    1: ">",
    2: "v",
    3: "<",
}


def color_block(color_code: int, text: str = "  ") -> str:
    return f"\x1b[48;5;{color_code}m{text}\x1b[0m"


def render(level: core.Level, use_color: bool) -> str:
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
            if use_color:
                if is_start:
                    row.append(color_block(COLOR_START, DIR_CHARS[core.NORTH_DIR] + " "))
                elif is_blocked:
                    row.append(color_block(COLOR_BLOCKED))
                else:
                    row.append(color_block(COLOR_EMPTY))
            else:
                if is_start:
                    row.append(DIR_CHARS[core.NORTH_DIR] + " ")
                elif is_blocked:
                    row.append("##")
                else:
                    row.append("..")
        lines.append("".join(row))

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Visualize a SenseJump level.")
    parser.add_argument("level", help="Level string or path to a .level file.")
    parser.add_argument("--no-color", action="store_true", help="Disable ANSI color output.")
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

    print(render(level, use_color))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
