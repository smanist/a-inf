from __future__ import annotations

import subprocess
from pathlib import Path

from a_inf import qmd


def isolate_qmd_home(tmp_path: Path, monkeypatch) -> Path:
    home = tmp_path / "home"
    monkeypatch.setattr(qmd.Path, "home", classmethod(lambda cls: home))
    return home


def test_ensure_qmd_collection_adds_missing_collection_and_syncs(tmp_path: Path, monkeypatch) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    isolate_qmd_home(tmp_path, monkeypatch)
    calls: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        if command[-1] == "--version":
            return subprocess.CompletedProcess(command, 0, stdout="qmd 2.1.0\n", stderr="")
        if command[1:] == ["collection", "show", "vault"]:
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="Collection not found")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(qmd.shutil, "which", lambda name: "/usr/local/bin/qmd" if name == "qmd" else None)
    monkeypatch.setattr(qmd.subprocess, "run", fake_run)

    assert qmd.ensure_qmd_collection(vault, {"QMD_WIKI_COLLECTION": "vault"}) is True
    assert (vault / ".a-inf" / "qmd" / "config" / "qmd").is_dir()
    assert (vault / ".a-inf" / "qmd" / "cache" / "qmd").is_dir()
    assert (vault / ".a-inf" / "qmd").is_dir()
    assert [call[1:] for call in calls] == [
        ["--version"],
        ["collection", "show", "vault"],
        ["collection", "add", str(vault), "--name", "vault"],
        ["--version"],
        ["update"],
        ["embed"],
    ]


def test_ensure_qmd_collection_skips_existing_collection(tmp_path: Path, monkeypatch) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    isolate_qmd_home(tmp_path, monkeypatch)
    calls: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="qmd 2.1.0\n", stderr="")

    monkeypatch.setattr(qmd.shutil, "which", lambda name: "/usr/local/bin/qmd" if name == "qmd" else None)
    monkeypatch.setattr(qmd.subprocess, "run", fake_run)

    assert qmd.ensure_qmd_collection(vault, {"QMD_WIKI_COLLECTION": "vault"}) is True
    assert [call[1:] for call in calls] == [
        ["--version"],
        ["collection", "show", "vault"],
        ["--version"],
        ["update"],
        ["embed"],
    ]


def test_sync_qmd_runs_update_then_embed(tmp_path: Path, monkeypatch) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    isolate_qmd_home(tmp_path, monkeypatch)
    calls: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="qmd 2.1.0\n", stderr="")

    monkeypatch.setattr(qmd.shutil, "which", lambda name: "/usr/local/bin/qmd" if name == "qmd" else None)
    monkeypatch.setattr(qmd.subprocess, "run", fake_run)

    assert qmd.sync_qmd(vault, {"QMD_WIKI_COLLECTION": "vault"}) is True
    assert [call[1:] for call in calls] == [["--version"], ["update"], ["embed"]]


def test_qmd_env_sets_xdg_paths(tmp_path: Path, monkeypatch) -> None:
    home = isolate_qmd_home(tmp_path, monkeypatch)

    env = qmd.qmd_env({"EXISTING": "1"})

    assert env["EXISTING"] == "1"
    assert env["XDG_CACHE_HOME"] == str(home / ".cache")
    assert env["XDG_CONFIG_HOME"] == str(home / ".config")


def test_qmd_env_sets_repo_local_paths(tmp_path: Path, monkeypatch) -> None:
    isolate_qmd_home(tmp_path, monkeypatch)
    vault = tmp_path / "vault"
    vault.mkdir()

    env = qmd.qmd_env({"EXISTING": "1"}, vault)

    assert env["EXISTING"] == "1"
    assert env["XDG_CACHE_HOME"] == str(vault / ".a-inf" / "qmd" / "cache")
    assert env["XDG_CONFIG_HOME"] == str(vault / ".a-inf" / "qmd" / "config")
    assert env["INDEX_PATH"] == str(vault / ".a-inf" / "qmd" / "index.sqlite")
