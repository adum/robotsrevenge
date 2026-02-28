#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import random
import re
import shlex
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import sensejump_core as core


@dataclass(frozen=True)
class GeneratorSpec:
    generator_id: str
    script_path: Path


DEFAULT_META_CONFIG_PATH = ROOT_DIR / "scripts" / "meta_generate_levels_config.json"


def resolve_path_from_root(value: str | Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return ROOT_DIR / path


def resolve_meta_config_path(argv: list[str]) -> Path:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--meta-config", type=Path, default=DEFAULT_META_CONFIG_PATH)
    parsed, _ = parser.parse_known_args(argv)
    return resolve_path_from_root(parsed.meta_config)


def load_meta_config(config_path: Path) -> tuple[dict[str, GeneratorSpec], dict[str, object]]:
    if not config_path.exists():
        raise ValueError(f"meta config file not found: {config_path}")
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in meta config: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError("meta config must be a JSON object")

    generators_raw = raw.get("generators")
    if not isinstance(generators_raw, list) or not generators_raw:
        raise ValueError("meta config 'generators' must be a non-empty list")

    generator_specs: dict[str, GeneratorSpec] = {}
    for entry in generators_raw:
        if not isinstance(entry, dict):
            raise ValueError("meta config generator entries must be objects")
        generator_id = entry.get("id")
        script_rel = entry.get("script")
        if not isinstance(generator_id, str) or not generator_id:
            raise ValueError("meta config generator 'id' must be a non-empty string")
        if generator_id in generator_specs:
            raise ValueError(f"duplicate generator id in meta config: {generator_id}")
        if not isinstance(script_rel, str) or not script_rel:
            raise ValueError(f"meta config generator '{generator_id}' has invalid 'script'")
        generator_specs[generator_id] = GeneratorSpec(
            generator_id=generator_id,
            script_path=resolve_path_from_root(script_rel),
        )

    defaults_raw = raw.get("defaults", {})
    if not isinstance(defaults_raw, dict):
        raise ValueError("meta config 'defaults' must be an object")

    default_generators = raw.get("default_generators")
    if default_generators is None:
        ordered_default_generators = list(generator_specs.keys())
    else:
        if not isinstance(default_generators, list) or not all(isinstance(item, str) for item in default_generators):
            raise ValueError("meta config 'default_generators' must be an array of strings")
        unknown = [generator_id for generator_id in default_generators if generator_id not in generator_specs]
        if unknown:
            raise ValueError(f"meta config 'default_generators' contains unknown ids: {', '.join(unknown)}")
        ordered_default_generators = list(default_generators)

    curated_copy_mode = str(defaults_raw.get("curated_copy_mode", "copy"))
    if curated_copy_mode not in ("copy", "hardlink"):
        raise ValueError("meta config defaults.curated_copy_mode must be 'copy' or 'hardlink'")
    start_size_raw = defaults_raw.get("start_size", 50)
    end_size_raw = defaults_raw.get("end_size", 300)
    if not isinstance(start_size_raw, int) or not isinstance(end_size_raw, int):
        raise ValueError("meta config defaults.start_size/end_size must be integers")
    if start_size_raw < 2 or end_size_raw < 2:
        raise ValueError("meta config defaults.start_size/end_size must be >= 2")
    if end_size_raw < start_size_raw:
        raise ValueError("meta config defaults.end_size must be >= defaults.start_size")
    attempts_per_level_raw = defaults_raw.get("attempts_per_level", None)
    if attempts_per_level_raw is not None:
        if not isinstance(attempts_per_level_raw, int):
            raise ValueError("meta config defaults.attempts_per_level must be an integer or null")
        if attempts_per_level_raw < 0:
            raise ValueError("meta config defaults.attempts_per_level must be >= 0 or null")

    defaults: dict[str, object] = {
        "default_generators": ordered_default_generators,
        "runs_per_generator": int(defaults_raw.get("runs_per_generator", 1)),
        "seed_step": int(defaults_raw.get("seed_step", 1_000_003)),
        "out_root": resolve_path_from_root(str(defaults_raw.get("out_root", "generated"))),
        "start_size": start_size_raw,
        "end_size": end_size_raw,
        "attempts_per_level": attempts_per_level_raw,
        "curated_id": str(defaults_raw.get("curated_id", "curated")),
        "curated_run_id": str(defaults_raw.get("curated_run_id", "run_001")),
        "curated_levels_dir": resolve_path_from_root(str(defaults_raw.get("curated_levels_dir", "levels"))),
        "curated_solutions_dir": resolve_path_from_root(str(defaults_raw.get("curated_solutions_dir", "solutions"))),
        "curated_copy_mode": curated_copy_mode,
    }
    return generator_specs, defaults


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_generators_arg(text: str) -> list[str]:
    result: list[str] = []
    for raw in text.split(","):
        value = raw.strip()
        if not value:
            continue
        if value.lower() == "none":
            continue
        result.append(value)
    return result


def parse_generator_arg_items(items: list[str]) -> dict[str, list[str]]:
    parsed: dict[str, list[str]] = {}
    for item in items:
        if ":" not in item:
            raise ValueError(f"Invalid --generator-arg '{item}'. Use format GENERATOR:ARG.")
        name, arg_text = item.split(":", 1)
        key = name.strip()
        arg = arg_text.strip()
        if not key or not arg:
            raise ValueError(f"Invalid --generator-arg '{item}'. Use format GENERATOR:ARG.")
        parsed.setdefault(key, []).append(arg)
    return parsed


def load_extra_arg_map(
    args_file: Path | None,
    inline_items: list[str],
) -> dict[str, list[str]]:
    merged: dict[str, list[str]] = {}

    if args_file is not None:
        raw = json.loads(args_file.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("--generator-args-file must contain a JSON object.")
        for key, value in raw.items():
            if not isinstance(key, str):
                raise ValueError("Generator-args keys must be strings.")
            if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                raise ValueError("Generator-args values must be arrays of strings.")
            merged.setdefault(key, []).extend(value)

    inline_map = parse_generator_arg_items(inline_items)
    for key, values in inline_map.items():
        merged.setdefault(key, []).extend(values)
    return merged


def shell_join(parts: list[str]) -> str:
    return " ".join(shlex.quote(part) for part in parts)


def copy_or_link(src: Path, dst: Path, mode: str) -> None:
    if mode == "hardlink":
        dst.hardlink_to(src)
    else:
        shutil.copy2(src, dst)


def has_flag(help_text: str, flag: str) -> bool:
    pattern = re.compile(rf"(?<![A-Za-z0-9_-]){re.escape(flag)}(?![A-Za-z0-9_-])")
    return pattern.search(help_text) is not None


def read_help_text(python_exec: str, script_path: Path) -> str:
    proc = subprocess.run(
        [python_exec, str(script_path), "--help"],
        cwd=ROOT_DIR,
        capture_output=True,
        text=True,
        check=False,
    )
    return (proc.stdout or "") + "\n" + (proc.stderr or "")


def level_id_from_name(name: str) -> int | None:
    stem = name.split(".", 1)[0]
    if not stem.isdigit():
        return None
    return int(stem)


def summarize_candidate(level_path: Path, solution_path: Path) -> dict[str, object]:
    summary: dict[str, object] = {
        "level_path": str(level_path),
        "solution_path": str(solution_path),
    }
    level_raw = level_path.read_text(encoding="utf-8")
    level = core.parse_level(level_raw)
    total_cells = max(1, level.width * level.height)
    density = 100.0 * core.block_count(level.board) / float(total_cells)

    summary["width"] = level.width
    summary["height"] = level.height
    summary["program_limit"] = level.program_limit
    summary["execution_limit"] = level.execution_limit
    summary["density_percent"] = round(density, 2)

    solution_data = json.loads(solution_path.read_text(encoding="utf-8"))
    summary["solution_steps"] = solution_data.get("solution_steps")
    summary["min_moves_to_exit"] = solution_data.get("min_moves_to_exit")
    summary["min_direction_types_to_exit"] = solution_data.get("min_direction_types_to_exit")
    summary["solution_hash"] = solution_data.get("solution_hash")
    summary["level_hash"] = solution_data.get("level_hash")
    return summary


def print_production_summary(meta_manifest: dict[str, object], out_root: Path) -> None:
    runs_raw = meta_manifest.get("runs", [])
    if not isinstance(runs_raw, list):
        print("Production summary: unavailable (invalid runs payload).", flush=True)
        return

    total_runs = len(runs_raw)
    total_ok = 0
    total_failed = 0
    total_incomplete = 0
    total_paired = 0
    total_expected = 0
    by_generator: dict[str, dict[str, int]] = {}

    for run in runs_raw:
        if not isinstance(run, dict):
            continue
        generator_id = str(run.get("generator", "unknown"))
        status = str(run.get("status", "unknown"))
        paired = int(run.get("paired", 0))
        expected = int(run.get("expected_count", 0))

        total_paired += paired
        total_expected += expected
        if status == "ok":
            total_ok += 1
        elif status == "incomplete":
            total_incomplete += 1
        else:
            total_failed += 1

        bucket = by_generator.setdefault(
            generator_id,
            {
                "runs": 0,
                "ok": 0,
                "incomplete": 0,
                "failed": 0,
                "paired": 0,
                "expected": 0,
            },
        )
        bucket["runs"] += 1
        bucket["paired"] += paired
        bucket["expected"] += expected
        if status == "ok":
            bucket["ok"] += 1
        elif status == "incomplete":
            bucket["incomplete"] += 1
        else:
            bucket["failed"] += 1

    print(
        "Production summary: "
        f"runs={total_runs} ok={total_ok} incomplete={total_incomplete} failed={total_failed} "
        f"paired={total_paired}/{total_expected}",
        flush=True,
    )
    for generator_id in sorted(by_generator):
        bucket = by_generator[generator_id]
        print(
            f"  {generator_id}: runs={bucket['runs']} ok={bucket['ok']} "
            f"incomplete={bucket['incomplete']} failed={bucket['failed']} "
            f"paired={bucket['paired']}/{bucket['expected']} "
            f"out={out_root / generator_id}",
            flush=True,
        )


def build_parser(
    generator_specs: dict[str, GeneratorSpec],
    static_defaults: dict[str, object],
    config_path: Path,
) -> argparse.ArgumentParser:
    default_generator_ids = static_defaults["default_generators"]
    if not isinstance(default_generator_ids, list) or not all(isinstance(item, str) for item in default_generator_ids):
        raise ValueError("invalid static default generator list")

    parser = argparse.ArgumentParser(
        description=(
            "Meta generator: run multiple generator scripts across the same level range. "
            "Outputs are stored per-generator/per-run under generated/<generator>/run_XXX/."
        )
    )
    parser.add_argument(
        "--meta-config",
        type=Path,
        default=config_path,
        help=f"Static config file path (default: {config_path}).",
    )
    parser.add_argument("max_level", type=int, nargs="?", default=None, help="Generate up to this level number.")
    parser.add_argument("--start-level", type=int, default=1, help="Starting level number (default: 1).")
    parser.add_argument(
        "--generators",
        type=str,
        default=",".join(default_generator_ids),
        help="Comma-separated generator IDs to run, or 'none' for curated-only.",
    )
    parser.add_argument(
        "--list-generators",
        action="store_true",
        help="List known generator IDs and exit.",
    )
    parser.add_argument(
        "--runs-per-generator",
        type=int,
        default=int(static_defaults["runs_per_generator"]),
        help="Number of runs per generator.",
    )
    parser.add_argument(
        "--out-root",
        type=Path,
        default=Path(static_defaults["out_root"]),
        help="Output root directory (default: generated).",
    )
    parser.add_argument(
        "--python",
        type=str,
        default=sys.executable,
        help="Python executable for child generator scripts (default: current interpreter).",
    )
    parser.add_argument("--seed", type=int, default=None, help="Optional base seed for deterministic run seeding.")
    parser.add_argument(
        "--seed-step",
        type=int,
        default=int(static_defaults["seed_step"]),
        help="Seed increment between runs when --seed is set (default: 1000003).",
    )

    parser.add_argument("--size", type=int, default=None, help="Fixed square size override.")
    parser.add_argument(
        "--min-size",
        type=int,
        default=int(static_defaults["start_size"]),
        help="Minimum size for progressive generators.",
    )
    parser.add_argument(
        "--max-size",
        type=int,
        default=int(static_defaults["end_size"]),
        help="Maximum size for progressive generators.",
    )
    parser.add_argument(
        "--progressive-total-levels",
        type=int,
        default=None,
        help="Global total level count for progression (when supported by generator).",
    )
    parser.add_argument("--min-program-length", type=int, default=None, help="Shared min program length override.")
    parser.add_argument("--max-program-length", type=int, default=None, help="Shared max program length override.")
    parser.add_argument("--density", type=float, default=None, help="Fixed density override (for single-density generators).")
    parser.add_argument("--min-density", type=float, default=None, help="Minimum density override (for oscillating generators).")
    parser.add_argument("--max-density", type=float, default=None, help="Maximum density override (for oscillating generators).")
    parser.add_argument("--best-of", type=int, default=None, help="Override best-of value where supported.")
    parser.add_argument(
        "--attempts-per-level",
        type=int,
        default=(
            int(static_defaults["attempts_per_level"])
            if static_defaults["attempts_per_level"] is not None
            else None
        ),
        help="Unified attempts cap mapped to --candidate-attempts or --max-attempts.",
    )
    parser.add_argument("--candidate-attempts", type=int, default=None, help="Override --candidate-attempts where supported.")
    parser.add_argument("--max-attempts", type=int, default=None, help="Override --max-attempts where supported.")
    parser.add_argument(
        "--min-direction-types-to-exit",
        type=int,
        default=None,
        help="Shared min direction types needed to exit.",
    )
    parser.add_argument(
        "--min-solution-direction-types",
        type=int,
        default=None,
        help="Shared min direction types used by hidden solution.",
    )
    parser.add_argument(
        "--max-straight-run",
        type=int,
        default=None,
        help="Shared straight-run rejection threshold.",
    )
    parser.add_argument(
        "--seal-unreachable",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Force seal-unreachable behavior when supported.",
    )
    parser.add_argument(
        "--texture-cleanup",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Force texture-cleanup behavior when supported.",
    )
    parser.add_argument(
        "--elim-from-solution-steps",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Force elim-from-solution-steps behavior when supported.",
    )

    parser.add_argument(
        "--generator-args-file",
        type=Path,
        default=None,
        help="JSON file mapping generator ID (or '*') to extra CLI args list.",
    )
    parser.add_argument(
        "--generator-arg",
        action="append",
        default=[],
        help="Single extra arg mapping in the form GENERATOR:ARG (can repeat).",
    )
    parser.add_argument(
        "--include-curated",
        action="store_true",
        help="Include hand-picked levels as an additional source under generated/curated/run_001.",
    )
    parser.add_argument(
        "--curated-id",
        type=str,
        default=str(static_defaults["curated_id"]),
        help="Generator/source ID label used for curated levels (default: curated).",
    )
    parser.add_argument(
        "--curated-run-id",
        type=str,
        default=str(static_defaults["curated_run_id"]),
        help="Run ID used for curated source output (default: run_001).",
    )
    parser.add_argument(
        "--curated-levels-dir",
        type=Path,
        default=Path(static_defaults["curated_levels_dir"]),
        help="Source directory containing curated *.level files (default: levels).",
    )
    parser.add_argument(
        "--curated-solutions-dir",
        type=Path,
        default=Path(static_defaults["curated_solutions_dir"]),
        help="Source directory containing curated *.solution.json files (default: solutions).",
    )
    parser.add_argument(
        "--curated-start-level",
        type=int,
        default=None,
        help="Start level index to import from curated source (default: --start-level).",
    )
    parser.add_argument(
        "--curated-max-level",
        type=int,
        default=None,
        help="Max level index to import from curated source (default: max_level).",
    )
    parser.add_argument(
        "--curated-copy-mode",
        choices=("copy", "hardlink"),
        default=str(static_defaults["curated_copy_mode"]),
        help="How curated files are materialized into output (default: copy).",
    )

    parser.add_argument("--pass-verbose", action="store_true", help="Pass --verbose to child generators when supported.")
    parser.add_argument("--dry-run", action="store_true", help="Print commands and manifests without running generators.")
    parser.add_argument("--fail-fast", action="store_true", help="Stop after the first failed run.")
    parser.add_argument(
        "--overwrite-run-dirs",
        action="store_true",
        help="Overwrite existing run directories if they already exist.",
    )
    parser.add_argument(
        "--require-complete-range",
        action="store_true",
        help="Mark a run as failed if any level in the requested range is missing.",
    )
    parser.add_argument(
        "--show-command",
        action="store_true",
        help="Print full child command lines before execution.",
    )
    return parser


def main(argv: list[str]) -> int:
    config_path = resolve_meta_config_path(argv)
    try:
        generator_specs, static_defaults = load_meta_config(config_path)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    try:
        parser = build_parser(generator_specs, static_defaults, config_path)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    args = parser.parse_args(argv)
    if resolve_path_from_root(args.meta_config) != config_path:
        print(
            "Error: --meta-config changed after pre-parse. "
            "Please pass a single --meta-config value.",
            file=sys.stderr,
        )
        return 2

    if args.list_generators:
        for generator_id in generator_specs:
            spec = generator_specs[generator_id]
            print(f"{generator_id}: {spec.script_path.relative_to(ROOT_DIR)}")
        return 0

    if args.max_level is None:
        print("Error: max_level is required unless --list-generators is set.", file=sys.stderr)
        return 2

    if args.start_level < 1:
        print("Error: --start-level must be >= 1.", file=sys.stderr)
        return 2
    if args.max_level < args.start_level:
        print("Error: max_level must be >= --start-level.", file=sys.stderr)
        return 2
    if args.runs_per_generator < 1:
        print("Error: --runs-per-generator must be >= 1.", file=sys.stderr)
        return 2
    if args.seed_step == 0:
        print("Error: --seed-step cannot be 0.", file=sys.stderr)
        return 2
    if args.attempts_per_level is not None and args.attempts_per_level < 0:
        print("Error: --attempts-per-level must be >= 0.", file=sys.stderr)
        return 2

    requested_generators = parse_generators_arg(args.generators)
    if not requested_generators and not args.include_curated:
        print("Error: --generators resolved to an empty list and curated source is disabled.", file=sys.stderr)
        return 2
    unknown = [name for name in requested_generators if name not in generator_specs]
    if unknown:
        print(f"Error: unknown generator IDs: {', '.join(unknown)}", file=sys.stderr)
        print(f"Known IDs: {', '.join(generator_specs.keys())}", file=sys.stderr)
        return 2
    if args.include_curated and args.curated_id in requested_generators:
        print(
            f"Error: curated source id '{args.curated_id}' conflicts with --generators entry.",
            file=sys.stderr,
        )
        return 2
    if args.include_curated and not args.curated_run_id.startswith("run_"):
        print("Error: --curated-run-id must start with 'run_' to match run folder conventions.", file=sys.stderr)
        return 2
    if args.include_curated and args.curated_start_level is not None and args.curated_start_level < 1:
        print("Error: --curated-start-level must be >= 1.", file=sys.stderr)
        return 2
    if args.include_curated and args.curated_max_level is not None and args.curated_max_level < 1:
        print("Error: --curated-max-level must be >= 1.", file=sys.stderr)
        return 2
    if (
        args.include_curated
        and args.curated_start_level is not None
        and args.curated_max_level is not None
        and args.curated_max_level < args.curated_start_level
    ):
        print("Error: --curated-max-level must be >= --curated-start-level.", file=sys.stderr)
        return 2

    try:
        extra_arg_map = load_extra_arg_map(args.generator_args_file, args.generator_arg)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    deterministic_seed_mode = args.seed is not None
    batch_seed = args.seed if deterministic_seed_mode else None
    effective_progressive_total_levels = (
        args.progressive_total_levels if args.progressive_total_levels is not None else args.max_level
    )
    args.out_root = resolve_path_from_root(args.out_root)
    args.curated_levels_dir = resolve_path_from_root(args.curated_levels_dir)
    args.curated_solutions_dir = resolve_path_from_root(args.curated_solutions_dir)
    args.out_root.mkdir(parents=True, exist_ok=True)

    meta_manifest: dict[str, object] = {
        "created_at": now_utc_iso(),
        "tool": "meta_generate_levels.py",
        "meta_config_path": str(config_path),
        "batch_seed": batch_seed,
        "static_defaults": {
            "default_generators": static_defaults["default_generators"],
            "runs_per_generator": static_defaults["runs_per_generator"],
            "seed_step": static_defaults["seed_step"],
            "out_root": str(static_defaults["out_root"]),
            "start_size": static_defaults["start_size"],
            "end_size": static_defaults["end_size"],
            "attempts_per_level": static_defaults["attempts_per_level"],
            "curated_id": static_defaults["curated_id"],
            "curated_run_id": static_defaults["curated_run_id"],
            "curated_levels_dir": str(static_defaults["curated_levels_dir"]),
            "curated_solutions_dir": str(static_defaults["curated_solutions_dir"]),
            "curated_copy_mode": static_defaults["curated_copy_mode"],
        },
        "config": {
            "start_level": args.start_level,
            "max_level": args.max_level,
            "runs_per_generator": args.runs_per_generator,
            "generators": requested_generators,
            "size": args.size,
            "min_size": args.min_size,
            "max_size": args.max_size,
            "progressive_total_levels": effective_progressive_total_levels,
            "min_program_length": args.min_program_length,
            "max_program_length": args.max_program_length,
            "density": args.density,
            "min_density": args.min_density,
            "max_density": args.max_density,
            "best_of": args.best_of,
            "attempts_per_level": args.attempts_per_level,
            "candidate_attempts": args.candidate_attempts,
            "max_attempts": args.max_attempts,
            "min_direction_types_to_exit": args.min_direction_types_to_exit,
            "min_solution_direction_types": args.min_solution_direction_types,
            "max_straight_run": args.max_straight_run,
            "seal_unreachable": args.seal_unreachable,
            "texture_cleanup": args.texture_cleanup,
            "elim_from_solution_steps": args.elim_from_solution_steps,
            "pass_verbose": args.pass_verbose,
            "dry_run": args.dry_run,
            "require_complete_range": args.require_complete_range,
            "seed_step": args.seed_step,
            "include_curated": args.include_curated,
            "curated_id": args.curated_id if args.include_curated else None,
            "curated_run_id": args.curated_run_id if args.include_curated else None,
            "curated_levels_dir": str(args.curated_levels_dir) if args.include_curated else None,
            "curated_solutions_dir": str(args.curated_solutions_dir) if args.include_curated else None,
            "curated_start_level": args.curated_start_level if args.include_curated else None,
            "curated_max_level": args.curated_max_level if args.include_curated else None,
            "curated_copy_mode": args.curated_copy_mode if args.include_curated else None,
        },
        "runs": [],
    }

    expected_ids = list(range(args.start_level, args.max_level + 1))
    expected_count = len(expected_ids)
    help_cache: dict[str, str] = {}

    def get_help_text(generator_id: str) -> str:
        text = help_cache.get(generator_id)
        if text is not None:
            return text
        spec = generator_specs[generator_id]
        text = read_help_text(args.python, spec.script_path)
        help_cache[generator_id] = text
        return text

    run_sequence = 0
    failed_runs = 0

    print(
        f"Meta generation: generators={','.join(requested_generators)}, runs_per_generator={args.runs_per_generator}, "
        f"levels={args.start_level}..{args.max_level}, out={args.out_root}, "
        f"batch_seed={batch_seed if batch_seed is not None else 'randomized'}"
    , flush=True)

    if args.include_curated:
        curated_start = args.curated_start_level if args.curated_start_level is not None else args.start_level
        curated_end = args.curated_max_level if args.curated_max_level is not None else args.max_level
        curated_start = max(curated_start, args.start_level)
        curated_end = min(curated_end, args.max_level)
        curated_expected_ids = list(range(curated_start, curated_end + 1)) if curated_start <= curated_end else []
        curated_expected_count = len(curated_expected_ids)

        if curated_expected_count == 0:
            print("Curated source enabled but selected range is empty; skipping curated import.", flush=True)
        else:
            if not args.curated_levels_dir.is_dir():
                print(f"Error: curated levels dir not found: {args.curated_levels_dir}", file=sys.stderr)
                return 2
            if not args.curated_solutions_dir.is_dir():
                print(f"Error: curated solutions dir not found: {args.curated_solutions_dir}", file=sys.stderr)
                return 2

            generator_id = args.curated_id
            run_id = args.curated_run_id
            run_sequence += 1

            gen_root = args.out_root / generator_id
            run_root = gen_root / run_id
            levels_dir = run_root / "levels"
            solutions_dir = run_root / "solutions"
            log_path = run_root / "generator.log"
            manifest_path = run_root / "run_manifest.json"

            if run_root.exists():
                if args.overwrite_run_dirs:
                    shutil.rmtree(run_root)
                else:
                    print(
                        f"Error: run directory already exists: {run_root}. "
                        "Use --overwrite-run-dirs to replace it.",
                        file=sys.stderr,
                    )
                    return 2

            run_root.mkdir(parents=True, exist_ok=True)
            levels_dir.mkdir(parents=True, exist_ok=True)
            solutions_dir.mkdir(parents=True, exist_ok=True)

            run_started = now_utc_iso()
            started_monotonic = time.monotonic()
            copied_ids: list[int] = []
            missing_source_ids: list[int] = []
            copy_errors: list[dict[str, object]] = []
            log_lines = [
                f"curated import: levels={curated_start}..{curated_end}",
                f"source levels dir: {args.curated_levels_dir}",
                f"source solutions dir: {args.curated_solutions_dir}",
                f"copy mode: {args.curated_copy_mode}",
            ]

            for level_id in curated_expected_ids:
                src_level = args.curated_levels_dir / f"{level_id}.level"
                src_solution = args.curated_solutions_dir / f"{level_id}.solution.json"
                dst_level = levels_dir / f"{level_id}.level"
                dst_solution = solutions_dir / f"{level_id}.solution.json"

                if not src_level.exists() or not src_solution.exists():
                    missing_source_ids.append(level_id)
                    log_lines.append(f"missing {level_id}: level={src_level.exists()} solution={src_solution.exists()}")
                    continue
                try:
                    copy_or_link(src_level, dst_level, args.curated_copy_mode)
                    copy_or_link(src_solution, dst_solution, args.curated_copy_mode)
                    copied_ids.append(level_id)
                except Exception as exc:  # noqa: BLE001
                    copy_errors.append({"level_id": level_id, "error": str(exc)})
                    log_lines.append(f"copy error {level_id}: {exc}")

            duration_sec = round(time.monotonic() - started_monotonic, 3)
            run_finished = now_utc_iso()
            log_path.write_text("\n".join(log_lines) + "\n", encoding="utf-8")

            found_level_files = sorted(levels_dir.glob("*.level"))
            found_solution_files = sorted(solutions_dir.glob("*.solution.json"))
            found_level_ids = {level_id_from_name(path.name) for path in found_level_files}
            found_solution_ids = {level_id_from_name(path.name) for path in found_solution_files}
            found_level_ids.discard(None)
            found_solution_ids.discard(None)

            paired_ids = sorted(int(level_id) for level_id in (found_level_ids & found_solution_ids))
            missing_ids = [level_id for level_id in curated_expected_ids if level_id not in (found_level_ids & found_solution_ids)]

            summaries: list[dict[str, object]] = []
            for level_id in paired_ids:
                level_path = levels_dir / f"{level_id}.level"
                solution_path = solutions_dir / f"{level_id}.solution.json"
                try:
                    level_summary = summarize_candidate(level_path, solution_path)
                    level_summary["level_id"] = level_id
                    summaries.append(level_summary)
                except Exception as exc:  # noqa: BLE001
                    summaries.append(
                        {
                            "level_id": level_id,
                            "level_path": str(level_path),
                            "solution_path": str(solution_path),
                            "summary_error": str(exc),
                        }
                    )

            status = "ok"
            if copy_errors:
                status = "failed"
            elif args.require_complete_range and len(paired_ids) != curated_expected_count:
                status = "incomplete"

            if status != "ok":
                failed_runs += 1
                print(
                    f"[{generator_id} {run_id}] status={status} paired={len(paired_ids)}/{curated_expected_count} "
                    f"log={log_path}"
                , flush=True)
                if args.fail_fast:
                    print("Fail-fast enabled; stopping.", flush=True)
            else:
                print(
                    f"[{generator_id} {run_id}] status=ok paired={len(paired_ids)}/{curated_expected_count} "
                    f"duration={duration_sec:.3f}s"
                , flush=True)

            command_text = (
                f"curated import from {args.curated_levels_dir} and {args.curated_solutions_dir}, "
                f"range={curated_start}..{curated_end}, mode={args.curated_copy_mode}"
            )
            run_manifest = {
                "generator": generator_id,
                "run_id": run_id,
                "script": "curated_import",
                "seed": None,
                "start_level": curated_start,
                "max_level": curated_end,
                "expected_count": curated_expected_count,
                "command": [],
                "command_text": command_text,
                "started_at": run_started,
                "finished_at": run_finished,
                "duration_sec": duration_sec,
                "return_code": 0 if status != "failed" else 1,
                "status": status,
                "counts": {
                    "level_files": len(found_level_files),
                    "solution_files": len(found_solution_files),
                    "paired": len(paired_ids),
                    "copied": len(copied_ids),
                },
                "missing_level_ids": missing_ids,
                "missing_source_ids": missing_source_ids,
                "copy_errors": copy_errors,
                "levels": summaries,
                "log_file": str(log_path),
                "source_levels_dir": str(args.curated_levels_dir),
                "source_solutions_dir": str(args.curated_solutions_dir),
                "copy_mode": args.curated_copy_mode,
            }
            manifest_path.write_text(json.dumps(run_manifest, indent=2), encoding="utf-8")

            meta_manifest["runs"].append(
                {
                    "generator": generator_id,
                    "run_id": run_id,
                    "status": status,
                    "return_code": 0 if status != "failed" else 1,
                    "duration_sec": duration_sec,
                    "manifest": str(manifest_path),
                    "log_file": str(log_path),
                    "paired": len(paired_ids),
                    "expected_count": curated_expected_count,
                }
            )

            if status != "ok" and args.fail_fast:
                meta_manifest["finished_at"] = now_utc_iso()
                meta_manifest["failed_runs"] = failed_runs
                meta_manifest_path = args.out_root / "meta_manifest.json"
                meta_manifest_path.write_text(json.dumps(meta_manifest, indent=2), encoding="utf-8")
                print(f"Wrote meta manifest: {meta_manifest_path}", flush=True)
                print_production_summary(meta_manifest, args.out_root)
                return 1

    for generator_id in requested_generators:
        spec = generator_specs[generator_id]
        if not spec.script_path.exists():
            print(f"Error: missing script {spec.script_path}", file=sys.stderr)
            return 2

        help_text = get_help_text(generator_id)

        for run_number in range(1, args.runs_per_generator + 1):
            run_sequence += 1
            run_id = f"run_{run_number:03d}"
            run_seed = None
            if deterministic_seed_mode and batch_seed is not None:
                run_seed = (batch_seed + (run_sequence - 1) * args.seed_step) % (2**63)

            gen_root = args.out_root / generator_id
            run_root = gen_root / run_id
            levels_dir = run_root / "levels"
            solutions_dir = run_root / "solutions"
            log_path = run_root / "generator.log"
            manifest_path = run_root / "run_manifest.json"

            if run_root.exists():
                if args.overwrite_run_dirs:
                    shutil.rmtree(run_root)
                else:
                    print(
                        f"Error: run directory already exists: {run_root}. "
                        "Use --overwrite-run-dirs to replace it.",
                        file=sys.stderr,
                    )
                    return 2

            run_root.mkdir(parents=True, exist_ok=True)
            levels_dir.mkdir(parents=True, exist_ok=True)
            solutions_dir.mkdir(parents=True, exist_ok=True)

            cmd_suffix: list[str] = [
                "--out-dir",
                str(levels_dir),
                "--solution-dir",
                str(solutions_dir),
            ]

            def add_flag_value(flag: str, value: object | None) -> None:
                if value is None:
                    return
                if has_flag(help_text, flag):
                    cmd_suffix.extend([flag, str(value)])

            def add_bool_flag(base: str, value: bool | None) -> None:
                if value is None:
                    return
                positive = f"--{base}"
                negative = f"--no-{base}"
                if value and has_flag(help_text, positive):
                    cmd_suffix.append(positive)
                elif not value and has_flag(help_text, negative):
                    cmd_suffix.append(negative)

            add_flag_value("--size", args.size)
            if args.size is None:
                add_flag_value("--min-size", args.min_size)
                add_flag_value("--max-size", args.max_size)

            add_flag_value("--progressive-start-level", args.start_level)
            add_flag_value("--progressive-total-levels", effective_progressive_total_levels)
            add_flag_value("--min-program-length", args.min_program_length)
            add_flag_value("--max-program-length", args.max_program_length)

            if args.density is not None:
                add_flag_value("--density", args.density)
            else:
                add_flag_value("--min-density", args.min_density)
                add_flag_value("--max-density", args.max_density)
                if has_flag(help_text, "--density") and args.min_density is not None and args.max_density is not None:
                    mid_density = 0.5 * (args.min_density + args.max_density)
                    add_flag_value("--density", mid_density)

            add_flag_value("--best-of", args.best_of)
            add_flag_value("--min-direction-types-to-exit", args.min_direction_types_to_exit)
            add_flag_value("--min-solution-direction-types", args.min_solution_direction_types)
            add_flag_value("--max-straight-run", args.max_straight_run)

            add_bool_flag("seal-unreachable", args.seal_unreachable)
            add_bool_flag("texture-cleanup", args.texture_cleanup)
            add_bool_flag("elim-from-solution-steps", args.elim_from_solution_steps)

            unified_attempts = args.attempts_per_level
            if args.candidate_attempts is not None:
                unified_attempts = args.candidate_attempts
            if args.max_attempts is not None:
                unified_attempts = args.max_attempts
            if unified_attempts is not None:
                if has_flag(help_text, "--candidate-attempts"):
                    cmd_suffix.extend(["--candidate-attempts", str(unified_attempts)])
                elif has_flag(help_text, "--max-attempts"):
                    cmd_suffix.extend(["--max-attempts", str(unified_attempts)])

            if args.pass_verbose and has_flag(help_text, "--verbose"):
                cmd_suffix.append("--verbose")

            for key in ("*", generator_id):
                for entry in extra_arg_map.get(key, []):
                    cmd_suffix.extend(shlex.split(entry))

            run_started = now_utc_iso()
            started_monotonic = time.monotonic()
            command_template = [args.python, str(spec.script_path), "{level_number}", *cmd_suffix]
            level_runs: list[dict[str, object]] = []
            launch_error: str | None = None
            return_code = 0
            log_lines: list[str] = [
                f"STARTED_AT: {run_started}",
                f"COMMAND_TEMPLATE: {shell_join(command_template)}",
                f"RUN_SEED: {run_seed if run_seed is not None else 'randomized'}",
            ]

            print(
                f"[{generator_id} {run_id}] seed={run_seed if run_seed is not None else 'randomized'}",
                flush=True,
            )
            for level_offset, level_id in enumerate(expected_ids):
                level_cmd = [args.python, str(spec.script_path), str(level_id), *cmd_suffix]
                level_seed = None
                if has_flag(help_text, "--seed"):
                    if run_seed is not None:
                        level_seed = (run_seed + level_offset * args.seed_step) % (2**63)
                    if level_seed is not None:
                        level_cmd.extend(["--seed", str(level_seed)])
                command_text = shell_join(level_cmd)
                log_lines.append(f"LEVEL {level_id} COMMAND: {command_text}")

                if args.show_command or args.dry_run:
                    print(f"  {command_text}", flush=True)

                level_return_code = 0
                level_launch_error: str | None = None
                if not args.dry_run:
                    try:
                        completed = subprocess.run(
                            level_cmd,
                            cwd=ROOT_DIR,
                            check=False,
                        )
                        level_return_code = completed.returncode
                    except OSError as exc:
                        level_return_code = 127
                        level_launch_error = str(exc)

                log_lines.append(f"LEVEL {level_id} RETURN_CODE: {level_return_code}")
                if level_launch_error is not None:
                    log_lines.append(f"LEVEL {level_id} LAUNCH_ERROR: {level_launch_error}")
                    if launch_error is None:
                        launch_error = level_launch_error

                level_runs.append(
                    {
                        "level_id": level_id,
                        "seed": level_seed,
                        "command": level_cmd,
                        "command_text": command_text,
                        "return_code": level_return_code,
                        "launch_error": level_launch_error,
                    }
                )

                if level_return_code != 0 and return_code == 0:
                    return_code = level_return_code
                if level_return_code != 0 and args.fail_fast:
                    break

            duration_sec = round(time.monotonic() - started_monotonic, 3)
            run_finished = now_utc_iso()
            log_lines.append(f"FINISHED_AT: {run_finished}")
            log_lines.append(f"DURATION_SEC: {duration_sec}")
            log_lines.append(f"RETURN_CODE: {return_code}")
            if launch_error is not None:
                log_lines.append(f"LAUNCH_ERROR: {launch_error}")
            log_path.write_text("\n".join(log_lines) + "\n", encoding="utf-8")

            found_level_files = sorted(levels_dir.glob("*.level"))
            found_solution_files = sorted(solutions_dir.glob("*.solution.json"))
            found_level_ids = {level_id_from_name(path.name) for path in found_level_files}
            found_solution_ids = {level_id_from_name(path.name) for path in found_solution_files}
            found_level_ids.discard(None)
            found_solution_ids.discard(None)

            paired_ids = sorted(int(level_id) for level_id in (found_level_ids & found_solution_ids))
            missing_ids = [level_id for level_id in expected_ids if level_id not in (found_level_ids & found_solution_ids)]

            summaries: list[dict[str, object]] = []
            for level_id in paired_ids:
                level_path = levels_dir / f"{level_id}.level"
                solution_path = solutions_dir / f"{level_id}.solution.json"
                try:
                    level_summary = summarize_candidate(level_path, solution_path)
                    level_summary["level_id"] = level_id
                    summaries.append(level_summary)
                except Exception as exc:  # noqa: BLE001
                    summaries.append(
                        {
                            "level_id": level_id,
                            "level_path": str(level_path),
                            "solution_path": str(solution_path),
                            "summary_error": str(exc),
                        }
                    )

            status = "ok"
            if return_code != 0:
                status = "failed"
            elif args.require_complete_range and len(paired_ids) != expected_count:
                status = "incomplete"

            if status != "ok":
                failed_runs += 1
                print(
                    f"  -> status={status} rc={return_code} paired={len(paired_ids)}/{expected_count} "
                    f"log={log_path}"
                , flush=True)
                if args.fail_fast:
                    print("Fail-fast enabled; stopping.", flush=True)
            else:
                print(f"  -> status=ok paired={len(paired_ids)}/{expected_count} duration={duration_sec:.3f}s", flush=True)

            run_manifest = {
                "generator": generator_id,
                "run_id": run_id,
                "script": str(spec.script_path.relative_to(ROOT_DIR)),
                "seed": run_seed,
                "start_level": args.start_level,
                "max_level": args.max_level,
                "expected_count": expected_count,
                "command_template": command_template,
                "level_runs": level_runs,
                "started_at": run_started,
                "finished_at": run_finished,
                "duration_sec": duration_sec,
                "return_code": return_code,
                "status": status,
                "counts": {
                    "level_files": len(found_level_files),
                    "solution_files": len(found_solution_files),
                    "paired": len(paired_ids),
                },
                "missing_level_ids": missing_ids,
                "levels": summaries,
                "log_file": str(log_path),
            }
            manifest_path.write_text(json.dumps(run_manifest, indent=2), encoding="utf-8")

            meta_manifest["runs"].append(
                {
                    "generator": generator_id,
                    "run_id": run_id,
                    "status": status,
                    "return_code": return_code,
                    "duration_sec": duration_sec,
                    "manifest": str(manifest_path),
                    "log_file": str(log_path),
                    "paired": len(paired_ids),
                    "expected_count": expected_count,
                }
            )

            if status != "ok" and args.fail_fast:
                meta_manifest["finished_at"] = now_utc_iso()
                meta_manifest["failed_runs"] = failed_runs
                meta_manifest_path = args.out_root / "meta_manifest.json"
                meta_manifest_path.write_text(json.dumps(meta_manifest, indent=2), encoding="utf-8")
                print(f"Wrote meta manifest: {meta_manifest_path}", flush=True)
                print_production_summary(meta_manifest, args.out_root)
                return 1

    meta_manifest["finished_at"] = now_utc_iso()
    meta_manifest["failed_runs"] = failed_runs
    meta_manifest["total_runs"] = len(meta_manifest["runs"])

    meta_manifest_path = args.out_root / "meta_manifest.json"
    meta_manifest_path.write_text(json.dumps(meta_manifest, indent=2), encoding="utf-8")
    print(f"Wrote meta manifest: {meta_manifest_path}", flush=True)
    print_production_summary(meta_manifest, args.out_root)

    if failed_runs > 0:
        print(f"Completed with failures: {failed_runs}/{len(meta_manifest['runs'])} runs not ok.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
