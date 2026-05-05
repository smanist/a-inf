from __future__ import annotations

import shutil
import subprocess
import sys
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class QmdInfo:
    binary: str
    version: str
    wiki_collection: str
    papers_collection: str
    index_path: str | None = None
    vault_path: str | None = None


def resolve_qmd(config: dict[str, str] | None = None, vault: Path | None = None) -> QmdInfo | None:
    config = config or {}
    qmd_bin = shutil.which("qmd")
    if qmd_bin is None:
        return None
    try:
        ensure_qmd_state_dirs(vault)
        result = subprocess.run(
            [qmd_bin, "--version"],
            check=False,
            capture_output=True,
            text=True,
            env=qmd_env(os.environ, vault),
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    output = (result.stdout or result.stderr).strip()
    version = output.splitlines()[0] if output else ""
    return QmdInfo(
        binary=qmd_bin,
        version=version,
        wiki_collection=config.get("QMD_WIKI_COLLECTION") or "",
        papers_collection=config.get("QMD_PAPERS_COLLECTION") or "",
        index_path=str(qmd_index_path(vault)) if vault is not None else None,
        vault_path=str(vault) if vault is not None else None,
    )


def require_qmd(qmd: QmdInfo | None) -> bool:
    if qmd is not None:
        return True
    print(
        "qmd executable not found or not usable. Install it with `npm install -g @tobilu/qmd`.",
        file=sys.stderr,
    )
    return False


def qmd_root(vault: Path) -> Path:
    return vault / ".a-inf" / "qmd"


def qmd_index_path(vault: Path | None = None) -> Path:
    if vault is not None:
        return qmd_root(vault) / "index.sqlite"
    if os.environ.get("INDEX_PATH"):
        return Path(os.environ["INDEX_PATH"]).expanduser()
    return qmd_cache_dir() / "index.sqlite"


def qmd_cache_dir(vault: Path | None = None) -> Path:
    if vault is not None:
        base = qmd_root(vault) / "cache"
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return base / "qmd"


def qmd_config_dir(vault: Path | None = None) -> Path:
    if vault is not None:
        base = qmd_root(vault) / "config"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "qmd"


def qmd_state_dirs(vault: Path | None = None) -> list[Path]:
    if vault is not None:
        return [qmd_root(vault)]
    return [qmd_config_dir(), qmd_cache_dir()]


def ensure_qmd_state_dirs(vault: Path | None = None) -> None:
    for directory in [qmd_config_dir(vault), qmd_cache_dir(vault), qmd_index_path(vault).parent]:
        directory.mkdir(parents=True, exist_ok=True)


def qmd_env(base_env: dict[str, str] | None = None, vault: Path | None = None) -> dict[str, str]:
    env = dict(base_env or {})
    env["XDG_CACHE_HOME"] = str(qmd_cache_dir(vault).parent)
    env["XDG_CONFIG_HOME"] = str(qmd_config_dir(vault).parent)
    if vault is not None:
        env["INDEX_PATH"] = str(qmd_index_path(vault))
    return env


def collection_name_for_vault(vault: Path, config: dict[str, str] | None = None) -> str:
    config = config or {}
    return config.get("QMD_WIKI_COLLECTION") or config.get("QMD_PAPERS_COLLECTION") or vault.name


def ensure_qmd_collection(vault: Path, config: dict[str, str] | None = None) -> bool:
    qmd = resolve_qmd(config, vault)
    if not require_qmd(qmd):
        return False

    name = collection_name_for_vault(vault, config)
    if run_qmd(qmd, ["collection", "show", name]).returncode == 0:
        return sync_qmd(vault, config)

    result = run_qmd(qmd, ["collection", "add", str(vault), "--name", name])
    if result.returncode != 0:
        print_qmd_failure("initialize QMD collection", result)
        return False
    return sync_qmd(vault, config)


def sync_qmd(vault: Path, config: dict[str, str] | None = None) -> bool:
    qmd = resolve_qmd(config, vault)
    if not require_qmd(qmd):
        return False

    for action in (["update"], ["embed"]):
        result = run_qmd(qmd, action)
        if result.returncode != 0:
            print_qmd_failure(f"run qmd {' '.join(action)}", result)
            return False
    return True


def run_qmd(qmd: QmdInfo, args: list[str]) -> subprocess.CompletedProcess[str]:
    vault = Path(qmd.vault_path) if qmd.vault_path else None
    return subprocess.run(
        [qmd.binary, *args],
        check=False,
        capture_output=True,
        text=True,
        env=qmd_env(os.environ, vault),
    )


def print_qmd_failure(action: str, result: subprocess.CompletedProcess[str]) -> None:
    message = (result.stderr or result.stdout).strip()
    if message:
        print(f"warning: failed to {action}: {message}", file=sys.stderr)
    else:
        print(f"warning: failed to {action}: exit code {result.returncode}", file=sys.stderr)
