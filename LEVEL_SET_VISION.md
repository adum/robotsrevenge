# Full Level Set Vision (Multi-Generator, ~1000 Levels)

## Objective
Produce a single high-quality, deployable level set of roughly 1000 levels by combining outputs from multiple generators, while preserving a coherent difficulty/size progression across level numbers.

## Core Strategy
Build the final set in two phases:
1. Meta-generation phase: run each generator independently over the same global level index range and size schedule, writing outputs into separate folders.
2. Assembly phase: merge those generated pools into one canonical `levels/` + `solutions/` sequence with meaningful alternation between generators.

Within phase 1, support repeated runs per generator over the same range so each level index can have multiple candidates. This enables per-level best-of selection before final assembly.

## Design Principles
- Diversity by construction: use multiple generator families so the final set has visibly different board structures and program behaviors.
- Size alignment by level index: for a given global level number, all generators target approximately the same board size.
- Approximate fairness, not rigidity: distribute selections across generators roughly evenly, but allow soft imbalance when quality or availability differs.
- Reproducibility: full run must be reproducible from seeds + config + manifests.
- Observable pipeline: every rejection/selection reason should be inspectable from logs/manifests.
- Quality first: a level is only eligible if it passes solvability and non-triviality checks.

## Scope
- Target output: about 1000 final levels.
- Generator pool (current): `v2`, `v3`, `v4_evo`, `v5_cegis`, `v5_dfs_decoy`, `v6_mcts`, `v7_mission_compiler`.
- Distribution goal: roughly even share across generators over the final set, without requiring exact equality.

## Unified Progression Contract
All generators participating in the campaign should be driven by the same global progression function for:
- target board size (primary shared axis)
- target solution/program band (optional shared axis)
- density driver band (optional shared axis)

This ensures level `N` from different generators is in the same broad size/difficulty neighborhood, making interleaving coherent.

## Phase 1: Meta-Generation
Create one orchestration script that runs each generator and writes per-generator artifacts.

### Responsibilities
- Define global run configuration: total levels, size curve, optional difficulty bands, seeds.
- Invoke each generator over the same level range (for example `1..1000`) with generator-specific flags.
- Support `runs_per_generator > 1` so each generator can be executed multiple times for the same index range.
- Store outputs in generator-specific folders.
- Emit per-generator manifests with metrics and provenance.

### Proposed Layout
```text
generated/
  v2/
    run_001/
      levels/
      solutions/
      manifest.json
    run_002/
      levels/
      solutions/
      manifest.json
  v3/
    run_001/
      levels/
      solutions/
      manifest.json
  v4_evo/
    run_001/
      levels/
      solutions/
      manifest.json
  ...
```

### Intra-Generator Best-Of Selection
- Add an optional reduction pass per generator after all runs complete.
- For each global level index, choose the best candidate among that generator's runs using configurable ranking:
  - hard gates first (must pass quality checks)
  - then score (for example min-moves-to-exit, route spread, instruction coverage, anti-triviality metrics)
- Emit a reduced manifest (for example `generated/v4_evo/best_of_manifest.json`) containing one selected candidate per level index where available.

### Required Metadata Per Generated Level
- generator id and version/script
- run id (for example `run_003`)
- global intended level index
- produced size and density
- solution length/steps and key quality metrics
- seed and generator parameters
- hashes (`level_hash`, `solution_hash`)
- reject/attempt summary for traceability

### Failure Policy
- A generator may fail some level indices.
- Failures are recorded, not hidden.
- Phase 2 handles gaps by selecting from available valid candidates.
- If all runs for a generator fail for a specific index, mark that index as missing in the generator's reduced manifest.

### Checker and Pruning Pass
- Add a separate checker script that scans generated artifacts and can invalidate/delete weak candidates before assembly.
- Checker should support explicit reject rules such as:
  - too easy to solve (for example short brute-force solution found)
  - low movement complexity
  - duplicate/near-duplicate structures
  - policy violations from current quality gates
- Prefer two modes:
  - report-only (no deletes, emits reasons)
  - enforce (removes or marks invalid candidates)
- Checker output should be machine-readable and feed into best-of reduction and final assembly.

## Phase 2: Assembly
Create a separate combiner script that builds final canonical folders.

### Responsibilities
- Read all per-generator reduced manifests (or raw manifests if reduction disabled) and candidate artifacts.
- For each global level index, choose one candidate level.
- Enforce soft generator balancing over the full run.
- Renumber/copy outputs into final folders.
- Emit a final manifest mapping source -> final level id.

### Proposed Final Layout
```text
final_levels/
  levels/
    1.level
    2.level
    ...
  solutions/
    1.solution.json
    2.solution.json
    ...
  manifest.json
```

### Selection Rules (Initial)
- Primary: candidate size proximity to target size for that global index.
- Secondary: quality score (non-triviality, movement complexity, route spread, etc.).
- Tertiary: generator balancing pressure (prefer underrepresented generators).
- Deduplicate near-identical boards/programs using hashes or similarity checks.

### Ordering Model
- Use weighted round-robin as default so generator types alternate.
- Allow local overrides when a generator has no valid candidate near the current target size.
- Keep alternation meaningful, not mechanically strict.

## Quality Gates for Final Inclusion
- Verified solvable by known solution.
- Passes current anti-degeneracy checks (meaningless jumps, dead instructions, trivial easy escapes, etc.).
- Meets required movement metrics (for example min moves to exit, min direction types to exit).
- Not a duplicate of already selected final levels.

## Reproducibility and Auditability
- Every phase writes machine-readable manifests.
- Final `manifest.json` must include:
  - final level id
  - source generator and source level id
  - source file hashes
  - target vs actual size
  - selected ranking/scores and reason codes
- Re-running with same config + seeds should reproduce the same final set.

## Practical Execution Plan
1. Build `meta_generate_levels.py` to run all generators into `generated/<generator>/run_XXX/...` with configurable runs per generator.
2. Build `check_generated_levels.py` for report/enforce quality pruning.
3. Build `reduce_generator_candidates.py` to perform per-generator per-level best-of selection across runs.
4. Build `assemble_level_set.py` to merge reduced pools into `final_levels/...`.
5. Start with a smaller pilot (for example 120 levels) to validate balancing, pruning, and size alignment.
6. Tune balancing weights and quality thresholds from pilot results.
7. Run the full ~1000-level campaign.

## Non-Goals
- Exact equal counts per generator.
- Perfectly monotonic difficulty in every adjacent level.
- Rewriting each generator to identical internals; only outputs must conform to shared campaign constraints.
