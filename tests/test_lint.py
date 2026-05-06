from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

from a_inf import cli
from a_inf import lint


def write_page(
    vault: Path,
    rel: str,
    *,
    title: str | None = None,
    tags: list[str] | None = None,
    summary: str | None = "summary",
    sources: list[str] | None = None,
    updated: str = "2026-05-05",
    lifecycle: str = "draft",
    base_confidence: float | str | None = 0.5,
    body: str = "",
    extra_frontmatter: list[str] | None = None,
) -> Path:
    path = vault / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    fm = [
        "---",
        f'title: "{title or path.stem}"',
        f'category: "{rel.split("/", 1)[0]}"',
        f"tags: {json.dumps(tags or ['test'])}",
    ]
    if summary is not None:
        fm.append(f'summary: "{summary}"')
    if sources is not None:
        fm.append(f"sources: {json.dumps(sources)}")
    else:
        fm.append("sources: [test]")
    fm.extend(
        [
            "created: 2026-05-05",
            f"updated: {updated}",
            f"lifecycle: {lifecycle}",
            "lifecycle_changed: 2026-05-05",
        ]
    )
    if base_confidence is not None:
        fm.append(f"base_confidence: {base_confidence}")
    if extra_frontmatter:
        fm.extend(extra_frontmatter)
    fm.extend(["---", "", body])
    path.write_text("\n".join(fm), encoding="utf-8")
    return path


def make_vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / ".manifest.json").write_text('{"version": 1}\n', encoding="utf-8")
    (vault / "log.md").write_text("# Wiki Log\n", encoding="utf-8")
    (vault / "index.md").write_text("# Wiki Index\n\n- [[concepts/a]] - A\n", encoding="utf-8")
    return vault


def lint_args(**overrides: object) -> SimpleNamespace:
    values = {
        "args": [],
        "json": False,
        "semantic_scope": "one-hop",
        "no_codex": True,
        "print_prompt": False,
        "no_log": True,
        "codex_bin": "codex",
        "sandbox": "workspace-write",
        "add_dir": [],
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_build_lint_packet_reports_hard_findings_and_candidates(tmp_path: Path) -> None:
    vault = make_vault(tmp_path)
    write_page(vault, "concepts/a.md", title="A", tags=["systems"], body="Links [[concepts/b]] and [[missing-page]].")
    write_page(vault, "concepts/b.md", title="B", tags=["systems"], summary=None, body="However this is unclear. ^[ambiguous]")
    write_page(vault, "concepts/c.md", title="C", tags=["systems"], body="In contrast this disagrees. ^[ambiguous]")
    for idx in range(3):
        write_page(vault, f"references/co-{idx}.md", body="Uses [[concepts/a]] and [[concepts/b]].")
    for idx in range(4):
        write_page(vault, f"skills/cluster-{idx}.md", tags=["systems"], body="")

    packet = lint.build_lint_packet(vault, {})

    assert packet["version"] == 1
    assert "concepts/a.md" in packet["page_registry"]
    assert packet["findings"]["broken_wikilinks"][0]["target"] == "missing-page"
    assert any(item["page"] == "concepts/b.md" for item in packet["findings"]["missing_summary"])
    assert packet["findings"]["fragmented_tag_clusters"][0]["tag"] == "systems"
    assert packet["candidates"]["contradiction_candidates"]
    assert packet["candidates"]["synthesis_gap_candidates"][0]["pair"] == ["concepts/a.md", "concepts/b.md"]


def test_lint_json_no_codex_outputs_packet_and_skips_semantic_review(tmp_path: Path, monkeypatch, capsys) -> None:
    vault = make_vault(tmp_path)
    write_page(vault, "concepts/a.md", body="See [[missing]].")
    monkeypatch.chdir(vault)

    result = cli.cmd_dispatch(lint_args(alias="lint", json=True))

    assert result == 0
    packet = json.loads(capsys.readouterr().out)
    assert packet["semantic_review"]["status"] == "not_run"
    assert packet["candidates"]["contradiction_candidates"] == []
    assert packet["findings"]["broken_wikilinks"][0]["target"] == "missing"


def test_lint_markdown_no_codex_hides_candidate_sections(tmp_path: Path, monkeypatch, capsys) -> None:
    vault = make_vault(tmp_path)
    write_page(vault, "concepts/a.md", body="See [[missing]].")
    monkeypatch.chdir(vault)

    result = cli.cmd_dispatch(lint_args(alias="lint", no_codex=True, no_log=True))

    assert result == 0
    output = capsys.readouterr().out
    assert "Broken Wikilinks" in output
    assert "Semantic review was not completed" in output
    assert "Potential Contradictions" not in output
    assert "contradiction_candidates" not in output


def test_default_lint_invokes_codex_with_one_hop_scope_and_logs(tmp_path: Path, monkeypatch, capsys) -> None:
    vault = make_vault(tmp_path)
    write_page(vault, "concepts/a.md", body="See [[missing]].")
    calls: list[list[str]] = []

    def fake_call(command: list[str], cwd: Path, env: dict[str, str] | None = None) -> int:
        calls.append(command)
        prompt = command[-1]
        review_path = Path(prompt.split("Write semantic review JSON to: ", 1)[1].splitlines()[0])
        review_path.write_text(
            json.dumps(
                {
                    "status": "completed",
                    "scope": "one-hop",
                    "findings": {"contradictions": [], "synthesis_gaps": []},
                    "repair_recommendations": [],
                    "reviewed_candidate_ids": [],
                    "warnings": [],
                }
            ),
            encoding="utf-8",
        )
        return 0

    monkeypatch.chdir(vault)
    monkeypatch.setattr(cli.shutil, "which", lambda _: "/usr/local/bin/codex")
    monkeypatch.setattr(lint.shutil, "which", lambda _: "/usr/local/bin/codex")
    monkeypatch.setattr(subprocess, "call", fake_call)

    result = cli.cmd_dispatch(lint_args(alias="lint", no_codex=False, no_log=False))

    assert result == 0
    assert calls
    assert "Semantic scope: one-hop" in calls[0][-1]
    assert "one-hop wikilink neighbors" in calls[0][-1]
    assert "semantic_review=completed" in (vault / "log.md").read_text(encoding="utf-8")
    assert "**Semantic review:** completed" in capsys.readouterr().out


def test_lint_broad_scope_appears_in_semantic_prompt(tmp_path: Path, monkeypatch, capsys) -> None:
    vault = make_vault(tmp_path)
    write_page(vault, "concepts/a.md")
    calls: list[list[str]] = []

    def fake_call(command: list[str], cwd: Path, env: dict[str, str] | None = None) -> int:
        calls.append(command)
        review_path = Path(command[-1].split("Write semantic review JSON to: ", 1)[1].splitlines()[0])
        review_path.write_text(
            json.dumps(
                {
                    "status": "completed",
                    "scope": "broad",
                    "findings": {"contradictions": [], "synthesis_gaps": []},
                    "repair_recommendations": [],
                    "reviewed_candidate_ids": [],
                    "warnings": [],
                }
            ),
            encoding="utf-8",
        )
        return 0

    monkeypatch.chdir(vault)
    monkeypatch.setattr(lint.shutil, "which", lambda _: "/usr/local/bin/codex")
    monkeypatch.setattr(subprocess, "call", fake_call)

    result = cli.cmd_dispatch(lint_args(alias="lint", no_codex=False, semantic_scope="broad"))

    assert result == 0
    assert "Semantic scope: broad" in calls[0][-1]
    assert "broadly across the vault" in calls[0][-1]
    capsys.readouterr()


def test_lint_no_log_leaves_log_unchanged(tmp_path: Path, monkeypatch, capsys) -> None:
    vault = make_vault(tmp_path)
    write_page(vault, "concepts/a.md")
    original = (vault / "log.md").read_text(encoding="utf-8")
    monkeypatch.chdir(vault)

    result = cli.cmd_dispatch(lint_args(alias="lint", no_log=True))

    assert result == 0
    assert (vault / "log.md").read_text(encoding="utf-8") == original
    capsys.readouterr()


def test_invalid_semantic_review_keeps_packet_output_valid(tmp_path: Path, monkeypatch, capsys) -> None:
    vault = make_vault(tmp_path)
    write_page(vault, "concepts/a.md")

    def fake_call(command: list[str], cwd: Path, env: dict[str, str] | None = None) -> int:
        review_path = Path(command[-1].split("Write semantic review JSON to: ", 1)[1].splitlines()[0])
        review_path.write_text("{not json", encoding="utf-8")
        return 0

    monkeypatch.chdir(vault)
    monkeypatch.setattr(lint.shutil, "which", lambda _: "/usr/local/bin/codex")
    monkeypatch.setattr(subprocess, "call", fake_call)

    result = cli.cmd_dispatch(lint_args(alias="lint", json=True, no_codex=False))

    assert result == 0
    packet = json.loads(capsys.readouterr().out)
    assert packet["semantic_review"]["status"] == "invalid"
    assert packet["findings"]["orphaned_pages"]


def test_orphan_pages_ignore_a_inf_tagged_files(tmp_path: Path) -> None:
    vault = make_vault(tmp_path)
    write_page(vault, "concepts/framework-note.md", tags=["a-inf"], body="")
    write_page(vault, "concepts/user-note.md", tags=["systems"], body="")

    packet = lint.build_lint_packet(vault, {})
    orphan_pages = {item["page"] for item in packet["findings"]["orphaned_pages"]}

    assert "concepts/framework-note.md" not in orphan_pages
    assert "concepts/user-note.md" in orphan_pages
