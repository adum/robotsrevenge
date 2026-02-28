#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import sensejump_core as core

if TYPE_CHECKING:
    import tkinter as tk
    from tkinter import ttk

tk = None  # type: ignore[assignment]
ttk = None  # type: ignore[assignment]

OPEN_RGB = (242, 244, 247)
BLOCKED_RGB = (24, 30, 39)
PATH_COLOR = "#ff7a00"
START_COLOR = "#1d9bf0"
STATUS_GOOD = "#0a7f2e"
STATUS_BAD = "#a31111"


@dataclass(frozen=True)
class LevelEntry:
    level_id: int
    level_path: Path
    solution_path: Path | None


@dataclass
class TraceResult:
    outcome: str
    steps: int
    path: list[tuple[int, int]]
    sensed_block_cells: set[tuple[int, int]]


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Browse generated levels in a native GUI window. "
            "By default, expects <root>/levels and <root>/solutions."
        )
    )
    parser.add_argument(
        "root",
        type=Path,
        nargs="?",
        default=Path("."),
        help="Root directory containing levels/ and solutions/ (default: current directory).",
    )
    parser.add_argument(
        "--levels-subdir",
        type=str,
        default="levels",
        help="Levels subdirectory under root (default: levels).",
    )
    parser.add_argument(
        "--solutions-subdir",
        type=str,
        default="solutions",
        help="Solutions subdirectory under root (default: solutions).",
    )
    parser.add_argument(
        "--start-level",
        type=int,
        default=None,
        help="Start browsing at this level id (default: first discovered level).",
    )
    parser.add_argument(
        "--hide-path",
        action="store_true",
        help="Do not render solution path overlay.",
    )
    return parser.parse_args(argv)


def discover_levels(levels_dir: Path, solutions_dir: Path) -> list[LevelEntry]:
    entries: list[LevelEntry] = []
    for level_file in sorted(levels_dir.glob("*.level"), key=lambda p: int(p.stem) if p.stem.isdigit() else 10**18):
        stem = level_file.stem
        if not stem.isdigit():
            continue
        level_id = int(stem)
        solution_path = solutions_dir / f"{level_id}.solution.json"
        if not solution_path.exists():
            solution_path = None
        entries.append(LevelEntry(level_id=level_id, level_path=level_file, solution_path=solution_path))
    return entries


def build_ppm(level: core.Level) -> bytes:
    width = level.width
    height = level.height
    header = f"P6 {width} {height} 255\n".encode("ascii")
    data = bytearray(width * height * 3)
    index = 0
    for y in range(height):
        row = level.board[y]
        for x in range(width):
            r, g, b = BLOCKED_RGB if row[x] else OPEN_RGB
            data[index] = r
            data[index + 1] = g
            data[index + 2] = b
            index += 3
    return header + data


def simulate_with_trace(level: core.Level, program: list[core.Instruction]) -> TraceResult:
    if not program:
        return TraceResult(
            outcome="invalid",
            steps=0,
            path=[(level.start_x, level.start_y)],
            sensed_block_cells=set(),
        )

    x = level.start_x
    y = level.start_y
    dir_index = core.NORTH_DIR
    pc = 0
    steps = 0
    n = len(program)
    limit = max(1, level.execution_limit)
    path: list[tuple[int, int]] = [(x, y)]
    sensed_block_cells: set[tuple[int, int]] = set()

    while steps < limit:
        inst = program[pc]
        op = inst.op.upper()

        if op == "F":
            dx, dy = core.DIR_DELTAS[dir_index]
            nx = x + dx
            ny = y + dy
            steps += 1
            if not core.in_bounds(nx, ny, level.width, level.height):
                return TraceResult(
                    outcome="escape",
                    steps=steps,
                    path=path,
                    sensed_block_cells=sensed_block_cells,
                )
            path.append((nx, ny))
            if level.board[ny][nx]:
                return TraceResult(
                    outcome="crash",
                    steps=steps,
                    path=path,
                    sensed_block_cells=sensed_block_cells,
                )
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
            nx = x + dx
            ny = y + dy
            blocked = core.in_bounds(nx, ny, level.width, level.height) and level.board[ny][nx]
            if blocked:
                sensed_block_cells.add((nx, ny))
            pc = core.wrap(pc + (1 if blocked else 2), n)
            steps += 1
            continue

        if op == "J":
            offset = inst.arg if isinstance(inst.arg, int) else 1
            if offset == 0:
                offset = 1
            pc = core.wrap(pc + offset, n)
            steps += 1
            continue

        return TraceResult(
            outcome="invalid",
            steps=steps,
            path=path,
            sensed_block_cells=sensed_block_cells,
        )

    return TraceResult(
        outcome="timeout",
        steps=steps,
        path=path,
        sensed_block_cells=sensed_block_cells,
    )


class LevelBrowserApp:
    def __init__(self, root: tk.Tk, entries: list[LevelEntry], start_level: int | None, show_path: bool) -> None:
        self.root = root
        self.entries = entries
        self.show_path = show_path
        self.level_cache: dict[int, core.Level] = {}
        self.solution_cache: dict[int, dict[str, object] | None] = {}
        self.trace_cache: dict[int, TraceResult | None] = {}

        self.current_index = 0
        if start_level is not None:
            for idx, entry in enumerate(entries):
                if entry.level_id == start_level:
                    self.current_index = idx
                    break

        self.photo_image: tk.PhotoImage | None = None

        self.root.title("Robot's Revenge Level Browser")
        self.root.geometry("1680x1020")

        self.top_label = ttk.Label(root, text="", anchor="w")
        self.top_label.pack(fill="x", padx=8, pady=(8, 4))

        main = ttk.Panedwindow(root, orient="horizontal")
        main.pack(fill="both", expand=True, padx=8, pady=4)

        left = ttk.Frame(main)
        right = ttk.Frame(main)
        main.add(left, weight=6)
        main.add(right, weight=1)

        self.canvas = tk.Canvas(left, background="#e6ebef", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", self._on_canvas_resize)

        self.info_text = tk.Text(right, wrap="word", font=("Consolas", 10))
        self.info_text.pack(fill="both", expand=True)
        self.info_text.configure(state="disabled")

        self.bottom_label = ttk.Label(
            root,
            text="Keys: Left/Right = prev/next level, Home/End = first/last, P = toggle path overlay, R = reload",
            anchor="w",
        )
        self.bottom_label.pack(fill="x", padx=8, pady=(4, 8))

        root.bind("<Left>", lambda _: self.prev_level())
        root.bind("<Right>", lambda _: self.next_level())
        root.bind("<Home>", lambda _: self.first_level())
        root.bind("<End>", lambda _: self.last_level())
        root.bind("<p>", lambda _: self.toggle_path())
        root.bind("<P>", lambda _: self.toggle_path())
        root.bind("<r>", lambda _: self.redraw_current())
        root.bind("<R>", lambda _: self.redraw_current())

        self.show_level(self.current_index)

    def _load_level(self, entry: LevelEntry) -> core.Level:
        cached = self.level_cache.get(entry.level_id)
        if cached is not None:
            return cached
        level = core.parse_level(entry.level_path.read_text(encoding="utf-8"))
        self.level_cache[entry.level_id] = level
        return level

    def _load_solution(self, entry: LevelEntry) -> dict[str, object] | None:
        if entry.level_id in self.solution_cache:
            return self.solution_cache[entry.level_id]
        if entry.solution_path is None:
            self.solution_cache[entry.level_id] = None
            return None
        try:
            payload = json.loads(entry.solution_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            payload = None
        self.solution_cache[entry.level_id] = payload
        return payload

    def _load_trace(self, entry: LevelEntry, level: core.Level, solution: dict[str, object] | None) -> TraceResult | None:
        if entry.level_id in self.trace_cache:
            return self.trace_cache[entry.level_id]
        trace: TraceResult | None = None
        if solution is not None:
            program_text = solution.get("solution_program")
            if isinstance(program_text, str) and program_text.strip():
                try:
                    program = core.parse_program_text(program_text)
                    trace = simulate_with_trace(level, program)
                except Exception:  # noqa: BLE001
                    trace = None
        self.trace_cache[entry.level_id] = trace
        return trace

    def _set_info_text(self, text: str) -> None:
        self.info_text.configure(state="normal")
        self.info_text.delete("1.0", "end")
        self.info_text.insert("1.0", text)
        self.info_text.configure(state="disabled")

    def _on_canvas_resize(self, _event: tk.Event) -> None:
        self.redraw_current()

    def redraw_current(self) -> None:
        self.show_level(self.current_index, force=True)

    def toggle_path(self) -> None:
        self.show_path = not self.show_path
        self.redraw_current()

    def prev_level(self) -> None:
        if self.current_index > 0:
            self.show_level(self.current_index - 1)

    def next_level(self) -> None:
        if self.current_index < len(self.entries) - 1:
            self.show_level(self.current_index + 1)

    def first_level(self) -> None:
        self.show_level(0)

    def last_level(self) -> None:
        self.show_level(len(self.entries) - 1)

    def show_level(self, index: int, force: bool = False) -> None:
        if index < 0 or index >= len(self.entries):
            return
        if not force and index == self.current_index:
            return
        self.current_index = index

        entry = self.entries[index]
        level = self._load_level(entry)
        solution = self._load_solution(entry)
        trace = self._load_trace(entry, level, solution)

        self._render_board(level, trace)
        self._render_info(entry, level, solution, trace)

    def _render_board(self, level: core.Level, trace: TraceResult | None) -> None:
        self.canvas.delete("all")
        canvas_w = max(10, self.canvas.winfo_width())
        canvas_h = max(10, self.canvas.winfo_height())

        base = tk.PhotoImage(data=build_ppm(level), format="PPM")
        margin = 24
        target_w = max(1, canvas_w - margin)
        target_h = max(1, canvas_h - margin)

        ratio = min(target_w / float(level.width), target_h / float(level.height))
        if ratio >= 1.0:
            up = max(1, int(math.floor(ratio)))
            shown = base.zoom(up, up) if up > 1 else base
        else:
            down = max(1, int(math.ceil(1.0 / ratio)))
            shown = base.subsample(down, down)

        self.photo_image = shown
        image_w = shown.width()
        image_h = shown.height()
        left = (canvas_w - image_w) // 2
        top = (canvas_h - image_h) // 2
        self.canvas.create_image(left, top, anchor="nw", image=shown)

        scale_x = image_w / float(level.width)
        scale_y = image_h / float(level.height)

        if self.show_path and trace is not None and trace.sensed_block_cells:
            sensed_color = "#7c7c7c"
            for x, y in trace.sensed_block_cells:
                if not core.in_bounds(x, y, level.width, level.height):
                    continue
                px0 = left + x * scale_x
                py0 = top + y * scale_y
                px1 = left + (x + 1) * scale_x
                py1 = top + (y + 1) * scale_y
                self.canvas.create_rectangle(px0, py0, px1, py1, fill=sensed_color, outline="")

        start_cx = left + (level.start_x + 0.5) * scale_x
        start_cy = top + (level.start_y + 0.5) * scale_y
        start_r = max(2.0, min(scale_x, scale_y) * 0.35)
        self.canvas.create_oval(
            start_cx - start_r,
            start_cy - start_r,
            start_cx + start_r,
            start_cy + start_r,
            fill=START_COLOR,
            outline="",
        )

        if self.show_path and trace is not None and len(trace.path) >= 2:
            points: list[float] = []
            for x, y in trace.path:
                if not core.in_bounds(x, y, level.width, level.height):
                    continue
                cx = left + (x + 0.5) * scale_x
                cy = top + (y + 0.5) * scale_y
                points.extend([cx, cy])
            if len(points) >= 4:
                width = max(1, int(min(scale_x, scale_y) * 0.30))
                self.canvas.create_line(*points, fill=PATH_COLOR, width=width, capstyle="round")

    def _render_info(
        self,
        entry: LevelEntry,
        level: core.Level,
        solution: dict[str, object] | None,
        trace: TraceResult | None,
    ) -> None:
        index = self.current_index + 1
        total = len(self.entries)
        density = 100.0 * core.block_count(level.board) / float(max(1, level.width * level.height))
        self.top_label.configure(
            text=(
                f"Level {entry.level_id} ({index}/{total})  "
                f"{level.width}x{level.height}  density={density:.1f}%  "
                f"program_limit={level.program_limit}  execution_limit={level.execution_limit}  "
                f"path={'on' if self.show_path else 'off'}"
            )
        )

        lines: list[str] = []
        lines.append(f"level_file: {entry.level_path}")
        lines.append(f"solution_file: {entry.solution_path if entry.solution_path is not None else '(missing)'}")
        lines.append("")

        if solution is None:
            lines.append("No solution JSON found for this level.")
            self._set_info_text("\n".join(lines))
            return

        steps = solution.get("solution_steps")
        min_moves = solution.get("min_moves_to_exit")
        min_dirs = solution.get("min_direction_types_to_exit")
        solution_hash = solution.get("solution_hash")
        level_hash = solution.get("level_hash")
        program_text = solution.get("solution_program", "")

        lines.append(f"solution_steps: {steps}")
        lines.append(f"min_moves_to_exit: {min_moves}")
        lines.append(f"min_direction_types_to_exit: {min_dirs}")
        lines.append(f"solution_hash: {solution_hash}")
        lines.append(f"level_hash: {level_hash}")

        if trace is not None:
            ok = trace.outcome == "escape"
            lines.append(
                f"simulated_outcome: {trace.outcome} ({trace.steps} steps)"
            )
            lines.append(f"path_cells: {len(trace.path)}")
            self.bottom_label.configure(
                text=(
                    "Keys: Left/Right = prev/next level, Home/End = first/last, "
                    "P = toggle path overlay, R = reload"
                ),
                foreground=STATUS_GOOD if ok else STATUS_BAD,
            )
        else:
            lines.append("simulated_outcome: unavailable")
            self.bottom_label.configure(
                text=(
                    "Keys: Left/Right = prev/next level, Home/End = first/last, "
                    "P = toggle path overlay, R = reload"
                ),
                foreground="",
            )

        lines.append("")
        lines.append("solution_program:")
        lines.append(str(program_text))
        self._set_info_text("\n".join(lines))


def main(argv: list[str]) -> int:
    args = parse_args(argv)

    global tk  # noqa: PLW0603
    global ttk  # noqa: PLW0603

    try:
        import tkinter as tk_mod
        from tkinter import ttk as ttk_mod
    except ModuleNotFoundError:
        print(
            "Error: tkinter is not installed for this Python. "
            "On Ubuntu/WSL install with: sudo apt-get install python3-tk",
            file=sys.stderr,
        )
        return 2

    tk = tk_mod
    ttk = ttk_mod
    root_dir = args.root.resolve()
    levels_dir = (root_dir / args.levels_subdir).resolve()
    solutions_dir = (root_dir / args.solutions_subdir).resolve()

    if not levels_dir.is_dir():
        print(f"Error: levels directory not found: {levels_dir}", file=sys.stderr)
        return 2
    if not solutions_dir.is_dir():
        print(f"Error: solutions directory not found: {solutions_dir}", file=sys.stderr)
        return 2

    entries = discover_levels(levels_dir, solutions_dir)
    if not entries:
        print(f"Error: no .level files found in {levels_dir}", file=sys.stderr)
        return 2

    tk_root = tk.Tk()
    app = LevelBrowserApp(
        root=tk_root,
        entries=entries,
        start_level=args.start_level,
        show_path=(not args.hide_path),
    )
    # Keep a reference to avoid lint "unused" style and ensure object lifetime.
    _ = app
    tk_root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
