from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path


RUNS_DIR = "_runs"


def runs_root(vault: Path) -> Path:
    return vault / RUNS_DIR


def unique_dir(base: Path) -> Path:
    suffix = 1
    candidate = base
    while candidate.exists():
        suffix += 1
        candidate = Path(f"{base}-{suffix}")
    candidate.mkdir(parents=True, exist_ok=True)
    return candidate


def timestamped_run_dir(vault: Path, prefix: str, *, fmt: str = "%Y%m%dT%H%M%SZ") -> Path:
    stamp = datetime.now(timezone.utc).strftime(fmt)
    name = f"{prefix}-{stamp}" if prefix else stamp
    return unique_dir(runs_root(vault) / name)

