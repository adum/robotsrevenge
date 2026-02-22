#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def run(cmd: list[str]) -> None:
    print("+", " ".join(cmd))
    subprocess.run(cmd, cwd=ROOT, check=True)


def ensure_clean_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build Cloudflare Pages distribution bundle for Robot's Revenge playable mode."
    )
    parser.add_argument("--out-dir", type=Path, default=Path("dist"), help="Output directory (default: dist).")
    parser.add_argument("--max-level", type=int, default=100, help="Generate up to this level id (default: 100).")
    parser.add_argument("--start-level", type=int, default=1, help="Starting level id (default: 1).")
    parser.add_argument("--seed", type=int, default=None, help="Optional deterministic generation seed.")
    parser.add_argument("--generate-levels", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--progressive-difficulty", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--size", type=int, default=11)
    parser.add_argument("--density", type=float, default=28.0)
    parser.add_argument("--solution-length", type=int, default=9)
    parser.add_argument("--program-limit", type=int, default=14)
    parser.add_argument("--execution-limit", type=int, default=420)
    parser.add_argument("--max-attempts", type=int, default=650)
    parser.add_argument("--max-straight-run", type=int, default=10)
    parser.add_argument("--min-direction-types-to-exit", type=int, default=2)
    parser.add_argument("--best-of", type=int, default=1)
    parser.add_argument("--level-seed-retries", type=int, default=0)
    parser.add_argument("--progressive-intensity", type=float, default=1.0)
    parser.add_argument("--progressive-max-size", type=int, default=128)
    parser.add_argument("--seal-unreachable", action=argparse.BooleanOptionalAction, default=True)
    return parser


def build_levels(args: argparse.Namespace, levels_out: Path, solutions_out: Path) -> None:
    cmd = [
        sys.executable,
        "generate_levels.py",
        str(args.max_level),
        "--start-level",
        str(args.start_level),
        "--out-dir",
        str(levels_out),
        "--solution-dir",
        str(solutions_out),
        "--size",
        str(args.size),
        "--density",
        str(args.density),
        "--solution-length",
        str(args.solution_length),
        "--program-limit",
        str(args.program_limit),
        "--execution-limit",
        str(args.execution_limit),
        "--max-attempts",
        str(args.max_attempts),
        "--max-straight-run",
        str(args.max_straight_run),
        "--min-direction-types-to-exit",
        str(args.min_direction_types_to_exit),
        "--best-of",
        str(args.best_of),
        "--level-seed-retries",
        str(args.level_seed_retries),
        "--progressive-intensity",
        str(args.progressive_intensity),
        "--progressive-max-size",
        str(args.progressive_max_size),
        "--no-show-reject-codes",
    ]
    if args.progressive_difficulty:
        cmd.append("--progressive-difficulty")
    if args.seed is not None:
        cmd.extend(["--seed", str(args.seed)])
    if not args.seal_unreachable:
        cmd.append("--no-seal-unreachable")
    run(cmd)


def write_manifest(levels_dir: Path, start_level: int, max_level: int) -> None:
    level_numbers = sorted(
        int(path.stem)
        for path in levels_dir.glob("*.level")
        if path.stem.isdigit()
    )
    if not level_numbers:
        raise RuntimeError(f"No level files found in {levels_dir}.")
    expected = list(range(start_level, max_level + 1))
    if level_numbers != expected:
        raise RuntimeError(
            "Generated levels are not contiguous: "
            f"expected {start_level}..{max_level}, got {level_numbers[0]}..{level_numbers[-1]} "
            f"({len(level_numbers)} files)."
        )
    manifest = {
        "version": 1,
        "start_level": start_level,
        "max_level": max_level,
        "count": len(level_numbers),
        "levels": level_numbers,
    }
    (levels_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def copy_runtime(out_dir: Path) -> None:
    shutil.copy2(ROOT / "play.html", out_dir / "index.html")
    shutil.copy2(ROOT / "play.js", out_dir / "play.js")
    shutil.copy2(ROOT / "play.css", out_dir / "play.css")
    shutil.copy2(ROOT / "index.css", out_dir / "index.css")
    shutil.copytree(ROOT / "assets", out_dir / "assets", dirs_exist_ok=True)


def copy_existing_levels(levels_out: Path, start_level: int, max_level: int) -> None:
    source_levels = ROOT / "levels"
    for level_number in range(start_level, max_level + 1):
        source = source_levels / f"{level_number}.level"
        if not source.exists():
            raise RuntimeError(f"Missing existing level: {source}")
        shutil.copy2(source, levels_out / source.name)


def main(argv: list[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.start_level < 1:
        print("Error: --start-level must be >= 1", file=sys.stderr)
        return 2
    if args.max_level < args.start_level:
        print("Error: --max-level must be >= --start-level", file=sys.stderr)
        return 2

    out_dir = ROOT / args.out_dir
    levels_out = out_dir / "levels"
    temp_solutions_out = out_dir / "_private_solutions"

    ensure_clean_dir(out_dir)
    levels_out.mkdir(parents=True, exist_ok=True)
    temp_solutions_out.mkdir(parents=True, exist_ok=True)

    if args.generate_levels:
        build_levels(args, levels_out, temp_solutions_out)
    else:
        copy_existing_levels(levels_out, args.start_level, args.max_level)

    shutil.rmtree(temp_solutions_out, ignore_errors=True)
    write_manifest(levels_out, args.start_level, args.max_level)
    copy_runtime(out_dir)

    print(
        f"Distribution ready at {out_dir} "
        f"(levels {args.start_level}..{args.max_level}, count={args.max_level - args.start_level + 1})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
