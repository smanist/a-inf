from __future__ import annotations

import subprocess
from pathlib import Path

from a_inf import cli
from a_inf import colorize
from a_inf import dashboard
from a_inf import ingest
from a_inf import insights
from a_inf import qmd


def isolate_qmd_home(tmp_path: Path, monkeypatch) -> Path:
    home = tmp_path / "qmd-home"
    monkeypatch.setattr(qmd.Path, "home", classmethod(lambda cls: home))
    return home


class Args:
    print_prompt = False
    no_codex = False
    codex_bin = "codex"
    sandbox = "workspace-write"
    add_dir: list[str] = []


def test_info_command_reports_effective_configuration(tmp_path: Path, monkeypatch, capsys) -> None:
    vault = tmp_path / "vault"
    sources = tmp_path / "configured-sources"
    vault.mkdir()
    sources.mkdir()
    (vault / ".manifest.json").write_text('{"version": 1}\n', encoding="utf-8")
    (vault / ".a-inf").mkdir()
    (vault / ".a-inf" / "config.toml").write_text(
        f'vault_path = "{vault}"\nskills_source = "{tmp_path / "skills"}"\nlink_format = "markdown"\n',
        encoding="utf-8",
    )
    (vault / ".env").write_text(
        f"OBSIDIAN_SOURCES_DIR={sources}\n"
        "OBSIDIAN_RAW_DIR=staging\n"
        "QMD_WIKI_COLLECTION=vault\n"
        "A_INF_SOURCE_ARCHIVE_DIR=archives\n",
        encoding="utf-8",
    )

    monkeypatch.chdir(vault)
    monkeypatch.setattr(ingest.Path, "home", classmethod(lambda cls: tmp_path / "home"))

    assert cli.main(["info"]) == 0
    packet = cli.json.loads(capsys.readouterr().out)
    assert packet["config_precedence"] == [".a-inf/config.toml", "~/.obsidian-wiki/config", ".env"]
    assert packet["config_files"][".a-inf/config.toml"]["values"]["link_format"] == "markdown"
    assert packet["effective_config"]["OBSIDIAN_SOURCES_DIR"] == str(sources)
    assert packet["effective_settings"]["link_format"] == "markdown"
    assert packet["effective_settings"]["ingest"]["source_roots"] == [str(sources)]
    assert packet["effective_settings"]["ingest"]["raw_dir"] == str(vault / "staging")
    assert packet["effective_settings"]["archive"]["source_archive_dir"] == str(vault / "archives")


def test_dispatch_invokes_codex_with_workspace_write_and_cd(
    tmp_path: Path, monkeypatch
) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / ".manifest.json").write_text('{"version": 1}\n', encoding="utf-8")
    isolate_qmd_home(tmp_path, monkeypatch)
    calls: list[tuple[list[str], Path, dict[str, str] | None]] = []

    monkeypatch.chdir(vault)
    monkeypatch.setattr(cli.shutil, "which", lambda _: "/usr/local/bin/codex")
    sync_calls: list[Path] = []
    monkeypatch.setattr(cli, "sync_qmd", lambda vault_arg, _config: sync_calls.append(vault_arg) or True)
    ensure_calls: list[Path] = []
    monkeypatch.setattr(cli, "ensure_qmd_collection", lambda vault_arg, _config: ensure_calls.append(vault_arg) or True)

    def fake_call(command: list[str], cwd: Path, env: dict[str, str] | None = None) -> int:
        calls.append((command, cwd, env))
        return 0

    monkeypatch.setattr(subprocess, "call", fake_call)

    result = cli.run_dispatch(cli.Dispatch("wiki-ingest", "prompt"), Args())

    assert result == 0
    assert calls[0][0] == [
        "/usr/local/bin/codex",
        "exec",
        "--sandbox",
        "workspace-write",
        "--cd",
        str(vault),
        "--add-dir",
        str(vault / ".a-inf" / "qmd"),
        "prompt",
    ]
    assert calls[0][1] == vault
    assert calls[0][2]["XDG_CACHE_HOME"] == str(vault / ".a-inf" / "qmd" / "cache")
    assert calls[0][2]["XDG_CONFIG_HOME"] == str(vault / ".a-inf" / "qmd" / "config")
    assert calls[0][2]["INDEX_PATH"] == str(vault / ".a-inf" / "qmd" / "index.sqlite")
    assert ensure_calls == [vault]
    assert sync_calls == [vault]


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
    monkeypatch.setattr(cli, "sync_qmd", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(cli, "ensure_qmd_collection", lambda *_args, **_kwargs: True)

    def fake_call(command: list[str], cwd: Path, env: dict[str, str] | None = None) -> int:
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
                "archive_dir": "_sources/example",
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
    assert "**Archived source detail layers:** 1" in output
    assert "**Ready to ingest:** 0 new + 0 modified = 0 sources" in output
    assert "**Recommendation:** No action" in output


def test_status_command_reports_pdf_sources(tmp_path: Path, monkeypatch, capsys) -> None:
    vault = tmp_path / "vault"
    sources = tmp_path / "sources"
    vault.mkdir()
    sources.mkdir()
    source = sources / "paper.pdf"
    source.write_bytes(b"%PDF source")
    (vault / ".manifest.json").write_text('{"version": 1, "sources": {}, "projects": {}, "stats": {}}\n', encoding="utf-8")
    (vault / ".env").write_text(f"OBSIDIAN_SOURCES_DIR={sources}\n", encoding="utf-8")

    monkeypatch.chdir(vault)
    monkeypatch.setattr(cli.shutil, "which", lambda _: (_ for _ in ()).throw(AssertionError("codex called")))

    result = cli.cmd_status(Args())

    assert result == 0
    output = capsys.readouterr().out
    assert "paper.pdf" in output
    assert "pdf" in output
    assert "**Ready to ingest:** 1 new + 0 modified = 1 sources" in output


def test_status_insights_routes_to_wiki_insights(
    tmp_path: Path, monkeypatch
) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / ".manifest.json").write_text('{"version": 1}\n', encoding="utf-8")
    calls: list[Path] = []

    class StatusArgs(Args):
        insights = True
        args: list[str] = []

    monkeypatch.chdir(vault)
    def fake_run(args: object, vault_arg: Path, config: dict[str, str]) -> int:
        calls.append(vault_arg)
        return 0

    monkeypatch.setattr(insights, "run_insights", fake_run)

    result = cli.cmd_status(StatusArgs())

    assert result == 0
    assert calls == [vault]


def test_cli_insights_dispatch_routes_to_engine(tmp_path: Path, monkeypatch) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / ".manifest.json").write_text('{"version": 1}\n', encoding="utf-8")
    calls: list[Path] = []

    class InsightArgs(Args):
        alias = "insights"
        args: list[str] = []
        json = True
        no_log = True

    def fake_run(args: object, vault_arg: Path, config: dict[str, str]) -> int:
        calls.append(vault_arg)
        return 0

    monkeypatch.chdir(vault)
    monkeypatch.setattr(insights, "run_insights", fake_run)

    result = cli.cmd_dispatch(InsightArgs())

    assert result == 0
    assert calls == [vault]


def test_cli_colorize_dispatch_routes_to_deterministic_engine(tmp_path: Path, monkeypatch) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / ".manifest.json").write_text('{"version": 1}\n', encoding="utf-8")
    calls: list[Path] = []

    class ColorizeArgs(Args):
        alias = "colorize"
        args: list[str] = []
        mode = "by-tag"
        groups_json = None
        json = True
        no_log = True

    def fake_run(args: object, vault_arg: Path, config: dict[str, str]) -> int:
        calls.append(vault_arg)
        return 0

    monkeypatch.chdir(vault)
    monkeypatch.setattr(colorize, "run_colorize", fake_run)
    monkeypatch.setattr(cli.shutil, "which", lambda _: (_ for _ in ()).throw(AssertionError("codex called")))

    result = cli.cmd_dispatch(ColorizeArgs())

    assert result == 0
    assert calls == [vault]


def test_cli_dashboard_dispatch_routes_to_deterministic_engine(tmp_path: Path, monkeypatch) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / ".manifest.json").write_text('{"version": 1}\n', encoding="utf-8")
    calls: list[Path] = []

    class DashboardArgs(Args):
        alias = "dashboard"
        args: list[str] = []
        recipe = "content-index"
        folder = None
        tag = None
        view = "table"
        name = None
        title = None
        limit = None
        json = True
        dry_run = True
        no_log = True

    def fake_run(args: object, vault_arg: Path, config: dict[str, str]) -> int:
        calls.append(vault_arg)
        return 0

    monkeypatch.chdir(vault)
    monkeypatch.setattr(dashboard, "run_dashboard", fake_run)
    monkeypatch.setattr(cli.shutil, "which", lambda _: (_ for _ in ()).throw(AssertionError("codex called")))

    result = cli.cmd_dispatch(DashboardArgs())

    assert result == 0
    assert calls == [vault]


def test_cli_parser_accepts_insights_flags() -> None:
    parser = cli.build_parser()

    args = parser.parse_args(["insights", "--json", "--no-log", "hubs"])

    assert args.alias == "insights"
    assert args.json is True
    assert args.no_log is True
    assert args.args == ["hubs"]


def test_cli_parser_accepts_colorize_flags() -> None:
    parser = cli.build_parser()

    args = parser.parse_args(["colorize", "--mode", "custom", "--groups-json", '{"tag:#ml":"blue"}', "--json"])

    assert args.alias == "colorize"
    assert args.mode == "custom"
    assert args.groups_json == '{"tag:#ml":"blue"}'
    assert args.json is True


def test_cli_parser_accepts_dashboard_flags() -> None:
    parser = cli.build_parser()

    args = parser.parse_args(
        [
            "dashboard",
            "--recipe",
            "stale-pages",
            "--folder",
            "concepts",
            "--tag",
            "ml",
            "--view",
            "cards",
            "--name",
            "ml-stale",
            "--title",
            "ML Stale",
            "--limit",
            "12",
            "--json",
            "--dry-run",
            "--no-log",
        ]
    )

    assert args.alias == "dashboard"
    assert args.recipe == "stale-pages"
    assert args.folder == "concepts"
    assert args.tag == "ml"
    assert args.view == "cards"
    assert args.name == "ml-stale"
    assert args.title == "ML Stale"
    assert args.limit == 12
    assert args.json is True
    assert args.dry_run is True
    assert args.no_log is True


def test_read_only_write_dispatch_skips_qmd_sync(tmp_path: Path, monkeypatch) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / ".manifest.json").write_text('{"version": 1}\n', encoding="utf-8")
    calls: list[tuple[list[str], Path]] = []

    class ReadOnlyArgs(Args):
        sandbox = "read-only"

    monkeypatch.chdir(vault)
    monkeypatch.setattr(cli.shutil, "which", lambda _: "/usr/local/bin/codex")
    monkeypatch.setattr(cli, "sync_qmd", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("qmd sync called")))
    monkeypatch.setattr(cli, "ensure_qmd_collection", lambda *_args, **_kwargs: True)

    def fake_call(command: list[str], cwd: Path, env: dict[str, str] | None = None) -> int:
        calls.append((command, cwd))
        return 0

    monkeypatch.setattr(subprocess, "call", fake_call)

    result = cli.run_dispatch(cli.Dispatch("wiki-update", "prompt"), ReadOnlyArgs())

    assert result == 0
    assert calls[0][0][2:4] == ["--sandbox", "read-only"]
