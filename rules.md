# SenseJump Robot Rules

## Objective
- Guide the robot to **escape** the grid without crashing into a blocked cell.
- The robot escapes when a `F` move would take it outside the board.

## Board
- Each cell is either empty `.` or blocked `X`.
- The robot starts on a specific cell, always facing **North**.

## Program Model
- The robot executes instructions from index `0`.
- The instruction list loops forever unless the robot escapes or crashes.
- Program counter wrapping uses modulo program length.

## Instructions
- `F` (Forward):
  Move one cell in the current facing direction.
  If target is outside the board, the robot escapes.
  If target is blocked, the robot crashes.
- `L` (Left):
  Rotate 90 degrees counter-clockwise.
- `R` (Right):
  Rotate 90 degrees clockwise.
- `S` (Sense):
  Look at the cell directly in front.
  If that cell is blocked, the **next** instruction is executed normally (`pc += 1`).
  Otherwise the next instruction is skipped (`pc += 2`).
  Out-of-bounds is treated as **not blocked** for sensing.
- `J±n` (Jump):
  Add signed offset `n` to the current program counter (`pc += n`) and wrap.

## End Conditions
- **Escape**: success.
- **Crash**: failure.
- **Step limit reached**: failure (prevents infinite loops).

## Level File Format (`.level`)
- Public level files use a single query-string style line:
  - `v=2&id=12&x=11&y=11&board=...,...,...&sx=5&sy=5&plim=14&elim=420&solhash=sha256:...`
- Fields:
  - `v`: format version.
  - `id`: level id.
  - `x`, `y`: width and height.
  - `board`: comma-separated rows using `.` (empty) and `X` (blocked).
  - `sx`, `sy`: start position (start direction is always North and is not encoded).
  - `plim`: program-length limit for this level.
  - `elim`: execution-step limit for this level.
  - `solhash`: sha256 hash of the canonical hidden solution text.

## Hidden Solution Format (`.solution.json`)
- Hidden solutions are stored separately from public levels:
  - Example filename: `12.solution.json`
- Core fields:
  - `v`, `id`
  - `level_hash`: hash of canonical public `.level` text.
  - `solution_hash`: hash of canonical `solution_program`.
  - `solution_program`: canonical instruction text (e.g. `F S J+2 R J-1`).
  - `solution_steps`: expected steps to escape.
  - `min_moves_to_exit`: shortest movement-only path length to leave the board from start.
  - `generator`: seed/settings metadata.

## Tooling
- Generate one level:
  - `python3 generate_level.py 12 --level-out levels/12.level --solution-out solutions/12.solution.json`
- By default, generators reject candidate levels whose hidden solution contains a straight run of `10` or more forward moves in one direction.
  - Tune or disable with `--max-straight-run N` (`0` disables).
- Generate a range:
  - `python3 generate_levels.py 50 --out-dir levels --solution-dir solutions`
  - `python3 generate_levels.py 50 --progressive-difficulty --size 9 --out-dir levels --solution-dir solutions`
  - `python3 generate_levels.py 1000 --progressive-difficulty --progressive-intensity 10 --size 9 --out-dir levels --solution-dir solutions`
  - Optional: `--progressive-max-size N` to cap progressive board growth (default: `128`).
  - Note: `generate_levels.py` always writes square boards (`width == height`).
- Verify a submission:
  - `python3 verify_level.py levels/12.level "F S J+2 R J-1"`
- Solve with brute force:
  - `python3 solve_level.py levels/12.level --timeout 60 --max-programs 5000000`
  - Add `--verbose` to print a traced board (path plus sensed blocked cells).
  - Optional shortcut: `python3 solve_level.py levels/12.level --solution-file solutions/12.solution.json`
    to evaluate only that provided solution (JSON `solution_program` or plain text file).
- Solve a directory in order (meta solver):
  - `python3 solve_levels.py solve_level.py --levels-dir levels --start 1 --end 50`
  - Extra args are forwarded to solver, e.g. `python3 solve_levels.py solve_level.py --levels-dir levels -- --max-length 8 --timeout 20`
- Verify official hidden solution too:
  - `python3 verify_level.py levels/12.level "F S J+2 R J-1" --solution-file solutions/12.solution.json`
  - `python3 verify_level.py levels/12.level --answer-dir solutions --official-only`
- Draw a level in terminal:
  - `python3 visualize_level.py levels/12.level`
