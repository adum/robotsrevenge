#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def ensure_clean_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build Cloudflare Pages distribution bundle for Robot's Revenge playable mode."
    )
    parser.add_argument("--out-dir", type=Path, default=Path("dist"), help="Output directory (default: dist).")
    return parser


def discover_source_levels(source_levels: Path) -> list[int]:
    if not source_levels.exists():
        raise RuntimeError(f"Levels directory not found: {source_levels}")
    level_numbers = sorted(int(path.stem) for path in source_levels.glob("*.level") if path.stem.isdigit())
    if not level_numbers:
        raise RuntimeError(f"No level files found in {source_levels}.")
    if level_numbers[0] != 1:
        raise RuntimeError(
            f"Levels must start at 1 for campaign mode, found first level {level_numbers[0]} in {source_levels}."
        )
    expected = list(range(level_numbers[0], level_numbers[-1] + 1))
    if level_numbers != expected:
        raise RuntimeError(
            "Levels are not contiguous: "
            f"expected {level_numbers[0]}..{level_numbers[-1]}, "
            f"but found {len(level_numbers)} files with gaps in {source_levels}."
        )
    return level_numbers


def write_manifest(levels_dir: Path, level_numbers: list[int]) -> None:
    start_level = level_numbers[0]
    max_level = level_numbers[-1]
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
    shutil.copy2(ROOT / "highscores.html", out_dir / "highscores.html")
    shutil.copy2(ROOT / "highscores.js", out_dir / "highscores.js")
    shutil.copy2(ROOT / "highscores.css", out_dir / "highscores.css")
    shutil.copy2(ROOT / "help.html", out_dir / "help.html")
    shutil.copy2(ROOT / "help.css", out_dir / "help.css")
    shutil.copy2(ROOT / "index.css", out_dir / "index.css")
    shutil.copytree(ROOT / "assets", out_dir / "assets", dirs_exist_ok=True)


def copy_existing_levels(levels_out: Path, source_levels: Path, level_numbers: list[int]) -> None:
    for level_number in level_numbers:
        source = source_levels / f"{level_number}.level"
        shutil.copy2(source, levels_out / source.name)


def main(argv: list[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    source_levels = ROOT / "levels"
    level_numbers = discover_source_levels(source_levels)

    out_dir = ROOT / args.out_dir
    levels_out = out_dir / "levels"
    ensure_clean_dir(out_dir)
    levels_out.mkdir(parents=True, exist_ok=True)

    copy_existing_levels(levels_out, source_levels, level_numbers)
    write_manifest(levels_out, level_numbers)
    copy_runtime(out_dir)

    print(
        f"Distribution ready at {out_dir} "
        f"(levels {level_numbers[0]}..{level_numbers[-1]}, count={len(level_numbers)})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
