# Generator Vision

## Goal
Build a level generator that produces solvable, varied, and interesting Robot's Revenge boards at scale, while keeping generation reproducible and debuggable.

The generator should prioritize levels where the hidden solution:
- Uses meaningful logic (not degenerate instruction patterns).
- Traverses a broad, non-trivial portion of the board.
- Feels puzzle-like, not obvious from a single visual cue.
- Can become progressively harder without a hard ceiling as level index increases.

## What We Are Trying To Achieve

### 1) Interesting gameplay, not geometric artifacts
- Avoid deterministic-looking outputs (exact spirals, simple staircases, straight lanes).
- Favor messy, organic layouts with multiple local structures.
- Keep hidden solution behavior non-trivial (branching via `S`, real control flow via `J`, directional variety).

### 2) Scalable generation
- Support both single-level generation and large batches.
- Handle large boards (200+) with practical runtime.
- Keep generation stable with bounded retries and clear failure reasons.

### 2.5) Near-unbounded difficulty scaling
- Difficulty growth is a first-class requirement, not a side effect.
- As level number increases, the system should support continuously harder instances (practically open-ended).
- Regenerating later level ranges should preserve the same difficulty profile those levels would have had in a full run.
- Hardness should increase across multiple dimensions, not only board size:
  - longer and more entangled hidden execution traces
  - higher ambiguity in locally “obvious” movement choices
  - larger search space for viable player programs
  - stricter movement and structure constraints (for example, direction-type requirements)

### 3) Reliable solvability and quality gates
- Every emitted level must have a verified hidden solution.
- Reject trivial or meaningless solutions:
  - meaningless jumps
  - dead/unused instructions
  - obvious easy escape programs
  - excessive straight runs (when enabled)
- Enforce movement-based quality metrics:
  - minimum moves to exit
  - minimum distinct direction types to exit

### 4) Reachable, clean final boards
- Final saved boards should not contain unreachable open noise.
- Keep `seal unreachable` behavior available (and normally on), but ensure resulting boards are still sufficiently open to be interesting.
- Balance openness so levels are neither corridor-only nor visually obvious.

## Required Properties

### Determinism and reproducibility
- Same seed + same config => same outputs.
- All key parameters captured in solution metadata.

### Observability
- Clear per-level constraints line before attempts.
- Live attempt status with reject counters (`sr`, `md`, `pl`, `ux`, etc.).
- Distinguish input drivers from final outcomes (for example, density driver vs final density).

### Compatibility
- Keep level and solution formats compatible with existing tools/UI.
- Preserve ability to generate both development and distribution-ready level sets.

### Tunability
- Expose the core controls needed for real-world runs:
  - size / progression
  - program length bounds
  - density/open-area controls
  - best-of selection
  - quality thresholds
  - difficulty-curve controls (so late-game hardness can be pushed much higher)

## Success Criteria
- Generated sets show strong visual and structural variety across seeds.
- Hidden solutions are consistently meaningful and use multiple instruction types.
- High-level progression feels fair: complexity rises without becoming generator-fragile.
- Operators can diagnose stalls quickly from live counters and constraints output.
- Late levels are measurably harder than early levels across stable metrics (not only subjective feel).

## Non-Goals
- Perfect human-rated difficulty prediction from generator alone.
- Single fixed aesthetic style for all levels.
- Overfitting to one generation strategy when alternatives produce better puzzle quality.
