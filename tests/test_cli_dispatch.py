from __future__ import annotations

import subprocess
from pathlib import Path

from a_inf import cli


class Args:
    print_prompt = False
    no_codex = False
    codex_bin = "codex"
    sandbox = "workspace-write"
    add_dir: list[str] = []


def test_dispatch_invokes_codex_with_workspace_write_and_cd(
    tmp_path: Path, monkeypatch
) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / ".manifest.json").write_text('{"version": 1}\n', encoding="utf-8")
    calls: list[tuple[list[str], Path]] = []

    monkeypatch.chdir(vault)
    monkeypatch.setattr(cli.shutil, "which", lambda _: "/usr/local/bin/codex")

    def fake_call(command: list[str], cwd: Path) -> int:
        calls.append((command, cwd))
        return 0

    monkeypatch.setattr(subprocess, "call", fake_call)

    result = cli.run_dispatch(cli.Dispatch("wiki-ingest", "prompt"), Args())

    assert result == 0
    assert calls == [
        (
            [
                "/usr/local/bin/codex",
                "exec",
                "--sandbox",
                "workspace-write",
                "--cd",
                str(vault),
                "prompt",
            ],
            vault,
        )
    ]


def test_status_dispatch_adds_codex_history_dir(
    tmp_path: Path, monkeypatch
) -> None:
    vault = tmp_path / "vault"
    history = tmp_path / "codex-history"
    vault.mkdir()
    history.mkdir()
    (vault / ".manifest.json").write_text('{"version": 1}\n', encoding="utf-8")
    (vault / ".env").write_text(f"CODEX_HISTORY_PATH={history}\n", encoding="utf-8")
    calls: list[tuple[list[str], Path]] = []

    monkeypatch.chdir(vault)
    monkeypatch.setattr(cli.shutil, "which", lambda _: "/usr/local/bin/codex")

    def fake_call(command: list[str], cwd: Path) -> int:
        calls.append((command, cwd))
        return 0

    monkeypatch.setattr(subprocess, "call", fake_call)

    result = cli.run_dispatch(cli.Dispatch("wiki-status", "prompt"), Args())

    assert result == 0
    assert "--add-dir" in calls[0][0]
    assert str(history) in calls[0][0]
