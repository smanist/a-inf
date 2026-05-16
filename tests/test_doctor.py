from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from a_inf import cli
from a_inf import doctor


def write_page(vault: Path, rel: str, *, title: str | None = None, body: str = "") -> Path:
    path = vault / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                "---",
                f'title: "{title or path.stem}"',
                f'category: "{rel.split("/", 1)[0]}"',
                'tags: ["test"]',
                "sources: [test]",
                'summary: "summary"',
                "created: 2026-05-05",
                "updated: 2026-05-05",
                "lifecycle: draft",
                "lifecycle_changed: 2026-05-05",
                "base_confidence: 0.5",
                "---",
                "",
                body,
            ]
        ),
        encoding="utf-8",
    )
    return path


def make_vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / ".manifest.json").write_text('{"version": 1}\n', encoding="utf-8")
    (vault / "index.md").write_text("# Wiki Index\n", encoding="utf-8")
    (vault / "log.md").write_text("# Wiki Log\n", encoding="utf-8")
    (vault / "hot.md").write_text("# Hot Cache\n", encoding="utf-8")
    return vault


def doctor_args(**overrides: object) -> SimpleNamespace:
    values = {
        "args": [],
        "alias": "doctor",
        "json": True,
        "dry_run": False,
        "fix": False,
        "full": False,
        "apply_tags": False,
        "semantic_scope": "one-hop",
        "no_codex": False,
        "print_prompt": False,
        "no_log": True,
        "codex_bin": "codex",
        "sandbox": "read-only",
        "add_dir": [],
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_doctor_default_removes_broken_links_and_saves_report(tmp_path: Path, capsys) -> None:
    vault = make_vault(tmp_path)
    source = write_page(vault, "references/source.md", body="Drop [[missing-page|Missing Label]].\n")

    result = doctor.run_doctor(doctor_args(), vault, {"OBSIDIAN_LINK_FORMAT": "wikilink"})

    assert result == 0
    assert "Drop Missing Label." in source.read_text(encoding="utf-8")
    packet = json.loads(capsys.readouterr().out)
    assert packet["summary"]["broken_wikilinks_before"] == 1
    assert packet["summary"]["broken_wikilinks_after"] == 0
    assert packet["summary"]["links_removed"] == 1
    assert packet["phases"][2]["name"] == "remove_broken"
    report_path = Path(packet["run_dir"]) / "doctor-report.md"
    assert report_path.exists()
    assert "Wiki Doctor Report" in report_path.read_text(encoding="utf-8")


def test_doctor_dry_run_does_not_edit_pages(tmp_path: Path, capsys) -> None:
    vault = make_vault(tmp_path)
    source = write_page(vault, "references/source.md", body="Drop [[missing-page|Missing Label]].\n")
    original = source.read_text(encoding="utf-8")

    result = doctor.run_doctor(doctor_args(dry_run=True), vault, {"OBSIDIAN_LINK_FORMAT": "wikilink"})

    assert result == 0
    assert source.read_text(encoding="utf-8") == original
    packet = json.loads(capsys.readouterr().out)
    assert packet["mode"]["dry_run"] is True
    assert packet["summary"]["broken_wikilinks_before"] == 1
    assert packet["summary"]["broken_wikilinks_after"] == 1
    assert packet["summary"]["links_removed"] == 0


def test_doctor_print_prompt_does_not_create_run_dir(tmp_path: Path, capsys) -> None:
    vault = make_vault(tmp_path)

    result = doctor.run_doctor(doctor_args(print_prompt=True), vault, {"OBSIDIAN_LINK_FORMAT": "wikilink"})

    assert result == 0
    packet = json.loads(capsys.readouterr().out)
    assert packet["command"] == "a-inf doctor"
    assert not (vault / "_runs").exists()


def test_cli_registers_doctor_parser_and_dispatch(tmp_path: Path, monkeypatch) -> None:
    parser = cli.build_parser()
    args = parser.parse_args(["doctor", "--dry-run", "--full", "--apply-tags", "--semantic-scope", "broad"])
    assert args.alias == "doctor"
    assert args.dry_run is True
    assert args.full is True
    assert args.apply_tags is True
    assert args.semantic_scope == "broad"

    vault = make_vault(tmp_path)
    calls: list[Path] = []

    def fake_run(args: object, vault_arg: Path, config: dict[str, str]) -> int:
        calls.append(vault_arg)
        return 0

    monkeypatch.chdir(vault)
    monkeypatch.setattr(doctor, "run_doctor", fake_run)

    result = cli.cmd_dispatch(doctor_args())

    assert result == 0
    assert calls == [vault]
