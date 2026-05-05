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


def test_history_dispatch_adds_codex_history_dir(
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

    result = cli.run_dispatch(cli.Dispatch("codex-history-ingest", "prompt"), Args())

    assert result == 0
    assert "--add-dir" in calls[0][0]
    assert str(history) in calls[0][0]


def test_status_command_reports_delta_without_codex(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    vault = tmp_path / "vault"
    sources = tmp_path / "sources"
    vault.mkdir()
    sources.mkdir()
    source = sources / "note.md"
    source.write_text("hello\n", encoding="utf-8")
    digest = cli.hash_file(source)
    modified_at = cli.format_datetime(cli.datetime.fromtimestamp(source.stat().st_mtime, cli.timezone.utc))
    manifest = {
        "version": 1,
        "last_updated": "2026-01-01T00:00:00+00:00",
        "sources": {
            str(source): {
                "ingested_at": "2026-01-01T00:00:00+00:00",
                "modified_at": modified_at,
                "content_hash": digest,
                "size_bytes": source.stat().st_size,
                "source_type": "document",
            }
        },
        "projects": {},
        "stats": {},
    }
    (vault / ".manifest.json").write_text(cli.json.dumps(manifest), encoding="utf-8")
    (vault / ".env").write_text(f"OBSIDIAN_SOURCES_DIR={sources}\n", encoding="utf-8")

    monkeypatch.chdir(vault)
    monkeypatch.setattr(cli.shutil, "which", lambda _: (_ for _ in ()).throw(AssertionError("codex called")))

    result = cli.cmd_status(Args())

    assert result == 0
    output = capsys.readouterr().out
    assert "# Wiki Status" in output
    assert "**Ready to ingest:** 0 new + 0 modified = 0 sources" in output
    assert "**Recommendation:** No action" in output


def test_status_insights_routes_to_wiki_insights(
    tmp_path: Path, monkeypatch
) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / ".manifest.json").write_text('{"version": 1}\n', encoding="utf-8")
    calls: list[tuple[list[str], Path]] = []

    class StatusArgs(Args):
        insights = True
        args: list[str] = []

    monkeypatch.chdir(vault)
    monkeypatch.setattr(cli.shutil, "which", lambda _: "/usr/local/bin/codex")

    def fake_call(command: list[str], cwd: Path) -> int:
        calls.append((command, cwd))
        return 0

    monkeypatch.setattr(subprocess, "call", fake_call)

    result = cli.cmd_status(StatusArgs())

    assert result == 0
    assert "Use the `wiki-insights` skill" in calls[0][0][-1]
