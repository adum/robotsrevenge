# Meta Generator Runbook

This document explains how to run and configure the new multi-generator pipeline:

1. `scripts/meta_generate_levels.py` (phase 1: produce candidate pools)
2. `scripts/check_generated_levels.py` (optional: check/prune weak candidates)
3. `scripts/reduce_generator_candidates.py` (phase 1.5: per-generator best-of across runs)
4. `scripts/assemble_level_set.py` (phase 2: combine best-of pools into final consecutive set)

This runbook covers exact commands, config files, and output structure.

## 1) Static Config File

Default static config path:

`scripts/meta_generate_levels_config.json`

This file contains non-dynamic setup:

- generator registry (`id` + script path)
- default generator order
- default static values (`runs_per_generator`, `seed_step`, output roots, curated defaults)

Current keys:

- `generators`: array of objects with:
- `id`: generator name used by `--generators`
- `script`: script path
- `default_generators`: ordered list used when `--generators` is not passed
- `defaults`:
- `runs_per_generator`
- `seed_step`
- `out_root`
- `start_size`
- `end_size`
- `attempts_per_level`
- `curated_id`
- `curated_run_id`
- `curated_levels_dir`
- `curated_solutions_dir`
- `curated_copy_mode`

## 2) CLI vs Config Precedence

For `meta_generate_levels.py`:

- static defaults are loaded from `--meta-config` (or default config file)
- CLI arguments override static defaults for that run
- dynamic values (seeds, run status, manifests, failures) are always runtime values

Example:

```bash
python3 scripts/meta_generate_levels.py \
  --meta-config scripts/meta_generate_levels_config.json \
  --list-generators
```

## 3) Phase 1: Generate Candidate Pools

### Quick command (all configured default generators)

```bash
python3 scripts/meta_generate_levels.py 1000 \
  --start-level 1 \
  --runs-per-generator 3 \
  --out-root generated_campaign \
  --seed 123456789
```

### Include curated early levels

This copies hand-picked levels/solutions into the same `generated/*/run_*` structure as a source named `curated` (or your custom ID).

```bash
python3 scripts/meta_generate_levels.py 1000 \
  --start-level 1 \
  --generators v4_evo,v5_cegis,v6_mcts,v7_mission_compiler \
  --runs-per-generator 3 \
  --include-curated \
  --curated-max-level 20 \
  --out-root generated_campaign \
  --seed 123456789
```

### Curated-only import

```bash
python3 scripts/meta_generate_levels.py 1000 \
  --generators none \
  --include-curated \
  --curated-max-level 20 \
  --out-root generated_campaign
```

### Dry-run (show planned commands)

```bash
python3 scripts/meta_generate_levels.py 100 \
  --start-level 1 \
  --runs-per-generator 2 \
  --show-command \
  --dry-run
```

### Generator output behavior

Child generator stdout/stderr is printed directly to terminal by default.
Use `--pass-verbose` to pass `--verbose` through to child generators that support it.

`generator.log` is meta-only and includes command/timing/exit status (it does not mirror child stdout/stderr).

```bash
python3 scripts/meta_generate_levels.py 50 \
  --start-level 1 \
  --generators v7_mission_compiler \
  --pass-verbose
```

## 4) Passing Shared Constraints

Common shared flags (only forwarded if the target generator supports them):

- size and progression: `--size`, `--min-size`, `--max-size`, `--progressive-total-levels`
- program length: `--min-program-length`, `--max-program-length`
- density: `--density` or `--min-density` + `--max-density`
- quality gates: `--min-direction-types-to-exit`, `--min-solution-direction-types`, `--max-straight-run`
- booleans: `--seal-unreachable`, `--texture-cleanup`, `--elim-from-solution-steps`
- attempts: `--attempts-per-level`, `--candidate-attempts`, `--max-attempts`

`meta_generate_levels.py` checks each generator's `--help` output and only passes supported flags.
By default, `--min-size/--max-size` come from config `defaults.start_size` and `defaults.end_size`.

## 5) Generator-Specific Extra Args

Use one or both:

- inline: `--generator-arg GENERATOR:ARG`
- file: `--generator-args-file path.json`

Inline example:

```bash
python3 scripts/meta_generate_levels.py 200 \
  --generators v7_mission_compiler,v6_mcts \
  --generator-arg v7_mission_compiler:--mission-nodes-min=6 \
  --generator-arg v7_mission_compiler:--mission-nodes-max=14 \
  --generator-arg v6_mcts:--mcts-iterations=1400
```

JSON file example (`/tmp/meta_args.json`):

```json
{
  "*": ["--best-of=1"],
  "v7_mission_compiler": ["--min-route-spread=0.05", "--min-steps-per-size=0.7"],
  "v6_mcts": ["--mcts-iterations=1200"]
}
```

Run:

```bash
python3 scripts/meta_generate_levels.py 300 \
  --generator-args-file /tmp/meta_args.json
```

## 6) Output Layout

Phase 1 outputs:

```text
generated_campaign/
  meta_manifest.json
  v4_evo/
    run_001/
      levels/
      solutions/
      run_manifest.json
      generator.log
    run_002/
      ...
  v5_cegis/
    run_001/
      ...
  curated/
    run_001/
      levels/
      solutions/
      run_manifest.json
      generator.log
```

Important manifests:

- top-level: `meta_manifest.json` (all runs + overall status)
- per-run: `run_manifest.json` (command, counts, missing ids, per-level summaries)

## 7) Phase 1B: Candidate Checking / Pruning

Report-only check:

```bash
python3 scripts/check_generated_levels.py \
  --root generated_campaign \
  --min-moves-to-exit 8 \
  --min-direction-types-to-exit 2 \
  --min-solution-steps 20 \
  --reject-easy-two-direction \
  --reject-meaningless-jump \
  --report generated_campaign/check_report.json
```

Enforce with delete:

```bash
python3 scripts/check_generated_levels.py \
  --root generated_campaign \
  --min-moves-to-exit 8 \
  --min-direction-types-to-exit 2 \
  --min-solution-steps 20 \
  --reject-easy-two-direction \
  --enforce \
  --delete-invalid \
  --report generated_campaign/check_report.json
```

Safe enforcement dry-run:

```bash
python3 scripts/check_generated_levels.py \
  --root generated_campaign \
  --enforce \
  --delete-invalid \
  --dry-run
```

## 8) Phase 1C: Per-Generator Best-Of Reduction

Select one candidate per level per generator across runs:

```bash
python3 scripts/reduce_generator_candidates.py \
  --root generated_campaign \
  --metric meander_score \
  --check-report generated_campaign/check_report.json \
  --overwrite
```

Metrics:

- `min_moves_to_exit`
- `solution_steps`
- `min_direction_types_to_exit`
- `meander_score` (default)
- `combined`

Output:

```text
generated_campaign/<generator>/best_of/
  levels/
  solutions/
  manifest.json
```

## 9) Phase 2: Final Assembly Across Generators

Combine all per-generator `best_of` outputs into one canonical final folder:

```bash
python3 scripts/assemble_level_set.py \
  --root generated_campaign \
  --levels-per-size-factor 2.0 \
  --overwrite
```

Reproducible run with explicit seed:

```bash
python3 scripts/assemble_level_set.py \
  --root generated_campaign \
  --levels-per-size-factor 2.0 \
  --seed 123456789 \
  --overwrite
```

Preview selection without writing files:

```bash
python3 scripts/assemble_level_set.py \
  --root generated_campaign \
  --levels-per-size-factor 2.0 \
  --dry-run
```

How `--levels-per-size-factor` works:

- integer part = guaranteed picks per size bucket
- fractional part = probabilistic extra pick per size bucket
- example: `2.3` means target 2 picks for every size, plus a 30% chance of 1 extra pick per size

Assembly output:

```text
generated_campaign/final/
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

Notes:

- Final level IDs are always consecutive starting at 1.
- Source level IDs are not preserved as final IDs; mapping is recorded in `manifest.json`.

## 10) Typical End-to-End Workflow

1. Generate candidates (all generators, multiple runs, curated early levels).
2. Run checker in report mode; inspect reject reasons.
3. Re-run checker in enforce mode if you want automatic pruning.
4. Run reducer to produce per-generator best-of pools.
5. Run assembler to produce `generated_campaign/final`.

## 11) Common Operational Flags

- `--fail-fast`: stop at first failing run
- `--overwrite-run-dirs`: overwrite existing run folders
- `--require-complete-range`: mark runs incomplete if any requested level is missing
- `--pass-verbose`: pass `--verbose` to child generators when supported
- `--show-command`: print full invoked child command lines

## 12) Troubleshooting Notes

- If a generator fails often, check terminal output first; `generator.log` contains command/timing/return code.
- If a flag appears ignored, confirm the target generator supports it in `--help`.
- If you need curated-only mode, use `--generators none --include-curated`.
- If paths in config are relative, they are resolved relative to repo root.
