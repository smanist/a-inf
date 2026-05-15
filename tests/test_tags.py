from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from a_inf import cli
from a_inf import tags


def write_page(
    vault: Path,
    rel: str,
    *,
    tag_values: list[str] | None = None,
    title: str | None = None,
    body: str = "",
) -> Path:
    path = vault / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    fm_tags = [] if tag_values is None else tag_values
    path.write_text(
        "\n".join(
            [
                "---",
                f'title: "{title or path.stem}"',
                f'category: "{rel.split("/", 1)[0]}"',
                f"tags: {json.dumps(fm_tags)}",
                "sources: [test]",
                'summary: "summary"',
                "created: 2026-05-05",
                "updated: 2026-05-05",
                "lifecycle: draft",
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
    (vault / "log.md").write_text("# Wiki Log\n", encoding="utf-8")
    (vault / "hot.md").write_text("# Hot Cache\n\n## Recent Activity\n", encoding="utf-8")
    (vault / "index.md").write_text("# Wiki Index\n", encoding="utf-8")
    (vault / "_meta").mkdir()
    (vault / "_meta" / "taxonomy.md").write_text(
        "\n".join(
            [
                "---",
                "title: Tag Taxonomy",
                "tags: [taxonomy, a-inf]",
                "---",
                "",
                "# Tag Taxonomy",
                "",
                "- `react` - Aliases: `nextjs`, `next-js`",
                "- `systems`",
                "- `ml`",
                "- `retro`",
                "- `generative-art`",
            ]
        ),
        encoding="utf-8",
    )
    return vault


def tags_args(**overrides: object) -> SimpleNamespace:
    values = {
        "args": [],
        "json": False,
        "fix": False,
        "plan": None,
        "no_codex": True,
        "print_prompt": False,
        "no_log": True,
        "codex_bin": "codex",
        "sandbox": "workspace-write",
        "add_dir": [],
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_build_tag_packet_reports_deterministic_findings(tmp_path: Path) -> None:
    vault = make_vault(tmp_path)
    write_page(vault, "concepts/a.md", tag_values=["nextjs", "systems", "systems", "Bad Tag", "visibility/internal", "visibility/public"])
    write_page(vault, "concepts/b.md", tag_values=["unknown"])
    write_page(vault, "concepts/c.md", tag_values=[])
    write_page(vault, "concepts/d.md", tag_values=["react", "systems", "ml", "retro", "generative-art", "extra"])

    packet = tags.build_tag_packet(vault, {}, vault / "_runs" / "tags" / "tag_plan.json")

    assert packet["summary"]["pages_scanned"] == 4
    assert packet["findings"]["alias_tags"] == [{"tag": "nextjs", "canonical": "react", "pages": 1}]
    assert {"tag": "unknown", "pages": 1, "recommendation": "map to closest canonical tag or skip"} in packet["findings"]["unknown_tags"]
    assert packet["findings"]["duplicate_tags"][0]["page"] == "concepts/a.md"
    assert packet["findings"]["malformed_tags"][0]["tag"] == "Bad Tag"
    assert packet["findings"]["over_tagged_pages"][0]["page"] == "concepts/d.md"
    assert packet["findings"]["untagged_pages"][0]["page"] == "concepts/c.md"
    assert packet["findings"]["visibility_issues"][0]["message"] == "multiple visibility tags"


def test_validate_tag_plan_rejects_invalid_edits(tmp_path: Path) -> None:
    vault = make_vault(tmp_path)
    write_page(vault, "concepts/a.md", tag_values=["nextjs"])

    base = {"version": 1, "status": "completed", "decisions": []}
    invalid_plans = [
        {**base, "decisions": [{"action": "update", "page": "missing.md", "expected_tags": [], "proposed_tags": ["react"]}]},
        {**base, "decisions": [{"action": "update", "page": "concepts/a.md", "expected_tags": ["old"], "proposed_tags": ["react"]}]},
        {**base, "decisions": [{"action": "update", "page": "concepts/a.md", "expected_tags": ["nextjs"], "proposed_tags": ["Bad Tag"]}]},
        {**base, "decisions": [{"action": "update", "page": "concepts/a.md", "expected_tags": ["nextjs"], "proposed_tags": ["react", "react"]}]},
        {
            **base,
            "decisions": [
                {
                    "action": "update",
                    "page": "concepts/a.md",
                    "expected_tags": ["nextjs"],
                    "proposed_tags": ["a", "b", "c", "d", "e", "f"],
                }
            ],
        },
    ]

    for plan in invalid_plans:
        with pytest.raises(tags.TagsError):
            tags.validate_tag_plan(plan, vault)


def test_fix_applies_latest_plan_and_updates_log_hot_and_taxonomy(tmp_path: Path, monkeypatch, capsys) -> None:
    vault = make_vault(tmp_path)
    write_page(vault, "concepts/a.md", tag_values=["nextjs", "visibility/internal"])
    run_dir = vault / "_runs" / "tags-20300101T000000Z"
    run_dir.mkdir(parents=True)
    (run_dir / "tag_plan.json").write_text(
        json.dumps(
            {
                "version": 1,
                "status": "completed",
                "decisions": [
                    {
                        "action": "update",
                        "page": "concepts/a.md",
                        "expected_tags": ["nextjs", "visibility/internal"],
                        "proposed_tags": ["react", "visibility/internal"],
                        "reason": "canonical alias",
                    },
                    {"action": "add_taxonomy_tag", "tag": "kubernetes", "aliases": ["k8s"], "reason": "used across infra notes"},
                ],
                "warnings": [],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(vault)
    monkeypatch.setattr(tags, "ensure_qmd_collection", lambda _vault, _config: False)

    result = cli.cmd_dispatch(tags_args(alias="tags", fix=True, no_log=False))

    assert result == 0
    assert 'tags: ["react", "visibility/internal"]' in (vault / "concepts/a.md").read_text(encoding="utf-8")
    assert "TAG_NORMALIZE" in (vault / "log.md").read_text(encoding="utf-8")
    assert "Tag normalization updated 1 pages" in (vault / "hot.md").read_text(encoding="utf-8")
    assert "`kubernetes`" in (vault / "_meta" / "taxonomy.md").read_text(encoding="utf-8")
    assert "Status:** completed" in capsys.readouterr().out


def test_cli_tags_no_codex_json_outputs_audit(tmp_path: Path, monkeypatch, capsys) -> None:
    vault = make_vault(tmp_path)
    write_page(vault, "concepts/a.md", tag_values=["nextjs"])
    monkeypatch.chdir(vault)

    result = cli.cmd_dispatch(tags_args(alias="tags", json=True, no_codex=True))

    assert result == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "not_run"
    assert report["summary"]["alias_tags_found"] == 1


def test_cli_tags_invokes_codex_plan_without_modifying_pages(tmp_path: Path, monkeypatch, capsys) -> None:
    vault = make_vault(tmp_path)
    page = write_page(vault, "concepts/a.md", tag_values=["nextjs"])
    before = page.read_text(encoding="utf-8")
    calls: list[list[str]] = []

    def fake_call(command: list[str], cwd: Path, env: dict[str, str] | None = None) -> int:
        calls.append(command)
        prompt = command[-1]
        plan_path = Path(prompt.split("Write editable tag plan JSON to: ", 1)[1].splitlines()[0])
        plan_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "status": "completed",
                    "decisions": [
                        {
                            "action": "update",
                            "page": "concepts/a.md",
                            "expected_tags": ["nextjs"],
                            "proposed_tags": ["react"],
                            "reason": "canonical alias",
                        }
                    ],
                    "warnings": [],
                }
            ),
            encoding="utf-8",
        )
        return 0

    monkeypatch.chdir(vault)
    monkeypatch.setattr(tags.shutil, "which", lambda _: "/usr/local/bin/codex")
    monkeypatch.setattr(subprocess, "call", fake_call)

    result = cli.cmd_dispatch(tags_args(alias="tags", no_codex=False))

    assert result == 0
    assert calls
    assert "Deterministic packet path:" in calls[0][-1]
    assert "Write editable tag plan JSON to:" in calls[0][-1]
    assert page.read_text(encoding="utf-8") == before
    assert "Status:** planned" in capsys.readouterr().out
