"""
Create memory restore point.

Key functions: now_stamp, copy_if_exists, main
"""
import argparse
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

import sys
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
import config as _cfg

WORKSPACE = _cfg.WORKSPACE
MEMORY_DIR = WORKSPACE / "memory"
REVIEW_DIR = MEMORY_DIR / "review"
LOGS_DIR = WORKSPACE / ".memory-index" / "logs"
RESTORE_DIR = LOGS_DIR / "restore-points"

# How many restore points to keep. One is written before every maintenance
# cycle and nothing ever removed them: 250 accumulated in 15 days and reached
# 15 GB, on a disk that went to 85% full. Recent ones are what anyone would
# actually roll back to.
KEEP_RESTORE_POINTS = int(os.environ.get("OPENCLAW_KEEP_RESTORE_POINTS", "20"))


def prune_restore_points(keep: int = KEEP_RESTORE_POINTS) -> int:
    """Delete all but the newest *keep* restore points. Returns how many went."""
    try:
        points = sorted(
            (d for d in RESTORE_DIR.iterdir() if d.is_dir()),
            key=lambda d: d.name,          # names are ISO timestamps, so sort==age
            reverse=True,
        )
    except FileNotFoundError:
        return 0
    removed = 0
    for stale in points[keep:]:
        shutil.rmtree(stale, ignore_errors=True)
        removed += 1
    return removed

RESTORE_DIR.mkdir(parents=True, exist_ok=True)


def now_stamp():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z").replace(":", "-")


def copy_if_exists(src: Path, dst: Path):
    if not src.exists():
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        shutil.copytree(src, dst, dirs_exist_ok=True)
    else:
        shutil.copy2(src, dst)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", default="manual")
    args = parser.parse_args()

    stamp = now_stamp()
    out_dir = RESTORE_DIR / f"restore-point-{stamp}-{args.label}"

    targets = [
        REVIEW_DIR / "canonical-items.jsonl",
        REVIEW_DIR / "canonical-superseded.jsonl",
        REVIEW_DIR / "pending-stable.jsonl",
        REVIEW_DIR / "inbox.jsonl",
        REVIEW_DIR / "auto.jsonl",
        REVIEW_DIR / "discarded.jsonl",
        REVIEW_DIR / "retrieval-feedback.jsonl",
        REVIEW_DIR / "decisions.log",
        MEMORY_DIR / "projects",
        MEMORY_DIR / "browser.md",
        MEMORY_DIR / "worker.md",
        MEMORY_DIR / "gateway.md",
        MEMORY_DIR / "preferences.md",
        MEMORY_DIR / "memory-system.md",
        MEMORY_DIR / "learned-fixes.md",
    ]

    for src in targets:
        rel = src.relative_to(WORKSPACE)
        dst = out_dir / rel
        copy_if_exists(src, dst)

    pruned = prune_restore_points()
    if pruned:
        print(f"pruned_old_restore_points={pruned}")

    print("RESTORE_POINT_CREATED")
    print(f"path={out_dir}")


if __name__ == "__main__":
    main()
