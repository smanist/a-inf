from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from a_inf import cli
from a_inf import fixlink
from a_inf import lint


def write_page(
    vault: Path,
    rel: str,
    *,
    title: str | None = None,
    tags: list[str] | None = None,
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
        "sources: [test]",
        f'summary: "{title or path.stem} summary"',
        "created: 2026-05-05",
        "updated: 2026-05-05",
        "lifecycle: draft",
        "lifecycle_changed: 2026-05-05",
        "base_confidence: 0.5",
    ]
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
    (vault / "hot.md").write_text("# Hot Cache\n\n## Recent Activity\n- Existing.\n", encoding="utf-8")
    return vault


def build_packet(vault: Path, config: dict[str, str] | None = None) -> dict[str, object]:
    config = config or {"OBSIDIAN_LINK_FORMAT": "wikilink"}
    lint_packet = lint.build_lint_packet(vault, config)
    return fixlink.build_fixlink_packet(vault, config, lint_packet, vault / "_runs" / "repair_plan.json")


def test_fixlink_candidates_include_exact_mentions_and_skip_existing_links(tmp_path: Path) -> None:
    vault = make_vault(tmp_path)
    write_page(vault, "concepts/target.md", title="Target Concept", tags=["systems"])
    write_page(vault, "references/source.md", title="Source", tags=["systems"], body="Target Concept appears in prose.")
    write_page(vault, "references/linked.md", title="Linked", tags=["systems"], body="Already [[concepts/target]]. Target Concept again.")
    for index in range(3):
        write_page(vault, f"references/cluster-{index}.md", title=f"Cluster {index}", tags=["systems"])

    packet = build_packet(vault)
    candidates = packet["candidates"]

    assert candidates[0]["candidate_id"] == "fixlink-0001"
    assert any(
        candidate["kind"] == "inline"
        and candidate["source"] == "references/source.md"
        and candidate["target"] == "concepts/target.md"
        for candidate in candidates
    )
    assert not any(
        candidate["source"] == "references/linked.md" and candidate["target"] == "concepts/target.md"
        for candidate in candidates
    )


def test_inline_matches_ignore_frontmatter_and_code_blocks(tmp_path: Path) -> None:
    vault = make_vault(tmp_path)
    write_page(vault, "concepts/target.md", title="Target Concept")
    write_page(
        vault,
        "references/source.md",
        title="Source",
        body='```text\nTarget Concept in code.\n```\n\nTarget Concept in prose.',
    )

    packet = build_packet(vault)
    candidate = next(candidate for candidate in packet["candidates"] if candidate["kind"] == "inline")

    assert [match["text"] for match in candidate["matches"]] == ["Target Concept"]
    assert candidate["matches"][0]["line"] > 10


def test_invalid_repair_plan_rejects_unknown_candidate_without_editing(tmp_path: Path) -> None:
    vault = make_vault(tmp_path)
    source = write_page(vault, "references/source.md", body="Target Concept appears.")
    write_page(vault, "concepts/target.md", title="Target Concept")
    packet = build_packet(vault)
    original = source.read_text(encoding="utf-8")

    with pytest.raises(fixlink.FixlinkError):
        fixlink.validate_repair_plan(
            {"version": 1, "status": "completed", "decisions": [{"candidate_id": "missing", "action": "add_inline"}]},
            packet,
            vault,
        )

    assert source.read_text(encoding="utf-8") == original


def test_apply_inline_and_related_wikilinks(tmp_path: Path) -> None:
    vault = make_vault(tmp_path)
    write_page(vault, "concepts/target.md", title="Target Concept", tags=["systems"])
    source = write_page(vault, "references/source.md", title="Source", tags=["systems"], body="Target Concept appears.")
    for index in range(5):
        write_page(vault, f"references/cluster-{index}.md", title=f"Cluster {index}", tags=["systems"])
    packet = build_packet(vault)
    inline = next(candidate for candidate in packet["candidates"] if candidate["kind"] == "inline")
    related = next(candidate for candidate in packet["candidates"] if candidate["kind"] == "related")
    plan = fixlink.validate_repair_plan(
        {
            "version": 1,
            "status": "completed",
            "decisions": [
                {"candidate_id": inline["candidate_id"], "action": "add_inline", "match_id": inline["matches"][0]["match_id"]},
                {"candidate_id": related["candidate_id"], "action": "add_related", "note": "Shares systems context."},
            ],
            "warnings": [],
        },
        packet,
        vault,
    )

    applied = fixlink.apply_repair_plan(plan, vault, {"OBSIDIAN_LINK_FORMAT": "wikilink"})

    assert applied["links_added"] == 2
    assert "[[concepts/target|Target Concept]] appears." in source.read_text(encoding="utf-8")
    related_text = (vault / related["source"]).read_text(encoding="utf-8")
    assert "## Related" in related_text
    assert f"[[{Path(related['target']).with_suffix('').as_posix()}" in related_text


def test_apply_inline_markdown_link_format(tmp_path: Path) -> None:
    vault = make_vault(tmp_path)
    write_page(vault, "concepts/target.md", title="Target Concept")
    source = write_page(vault, "references/source.md", body="Target Concept appears.")
    packet = build_packet(vault, {"OBSIDIAN_LINK_FORMAT": "markdown"})
    inline = next(candidate for candidate in packet["candidates"] if candidate["kind"] == "inline")
    plan = fixlink.validate_repair_plan(
        {
            "version": 1,
            "status": "completed",
            "decisions": [
                {"candidate_id": inline["candidate_id"], "action": "add_inline", "match_id": inline["matches"][0]["match_id"]}
            ],
            "warnings": [],
        },
        packet,
        vault,
    )

    fixlink.apply_repair_plan(plan, vault, {"OBSIDIAN_LINK_FORMAT": "markdown"})

    assert "[Target Concept](../concepts/target.md) appears." in source.read_text(encoding="utf-8")


def test_run_fixlink_dry_run_validates_without_editing(tmp_path: Path, monkeypatch, capsys) -> None:
    vault = make_vault(tmp_path)
    source = write_page(vault, "references/source.md", body="Target Concept appears.")
    write_page(vault, "concepts/target.md", title="Target Concept")
    original = source.read_text(encoding="utf-8")

    def fake_call(command: list[str], cwd: Path, env: dict[str, str] | None = None) -> int:
        prompt = command[-1]
        plan_path = Path(prompt.split("Write repair plan JSON to: ", 1)[1].splitlines()[0])
        packet_path = Path(prompt.split("Deterministic packet path: ", 1)[1].splitlines()[0])
        packet = json.loads(packet_path.read_text(encoding="utf-8"))
        inline = next(candidate for candidate in packet["candidates"] if candidate["kind"] == "inline")
        plan_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "status": "completed",
                    "decisions": [
                        {
                            "candidate_id": inline["candidate_id"],
                            "action": "add_inline",
                            "match_id": inline["matches"][0]["match_id"],
                        }
                    ],
                    "warnings": [],
                }
            ),
            encoding="utf-8",
        )
        return 0

    monkeypatch.setattr(fixlink.shutil, "which", lambda _: "/usr/local/bin/codex")
    monkeypatch.setattr(subprocess, "call", fake_call)

    result = fixlink.run_fixlink(
        SimpleNamespace(
            print_prompt=False,
            no_codex=False,
            dry_run=True,
            json=True,
            no_log=True,
            codex_bin="codex",
            sandbox="workspace-write",
            add_dir=[],
        ),
        vault,
        {"OBSIDIAN_LINK_FORMAT": "wikilink"},
    )

    assert result == 0
    assert source.read_text(encoding="utf-8") == original
    assert json.loads(capsys.readouterr().out)["status"] == "dry_run"


def test_remove_broken_links_is_deterministic_and_skips_codex(tmp_path: Path, monkeypatch, capsys) -> None:
    vault = make_vault(tmp_path)
    write_page(vault, "concepts/existing.md", title="Existing")
    source = write_page(
        vault,
        "references/source.md",
        body=(
            "Keep [[concepts/existing|Existing Link]].\n"
            "Drop [[missing-page|Missing Label]] and [[folder/other.md]].\n"
            "```text\n"
            "[[code-only-missing]]\n"
            "```\n"
        ),
    )

    monkeypatch.setattr(fixlink.shutil, "which", lambda _: (_ for _ in ()).throw(AssertionError("codex called")))

    result = fixlink.run_fixlink(
        SimpleNamespace(
            print_prompt=False,
            no_codex=False,
            dry_run=False,
            remove_broken=True,
            json=True,
            no_log=True,
            codex_bin="codex",
            sandbox="read-only",
            add_dir=[],
        ),
        vault,
        {"OBSIDIAN_LINK_FORMAT": "wikilink"},
    )

    assert result == 0
    text = source.read_text(encoding="utf-8")
    assert "Keep [[concepts/existing|Existing Link]]." in text
    assert "Drop Missing Label and other." in text
    assert "[[code-only-missing]]" in text
    report = json.loads(capsys.readouterr().out)
    assert report["mode"] == "remove_broken"
    assert report["summary"]["links_removed"] == 2
    assert report["summary"]["pages_modified"] == 1


def test_remove_broken_links_dry_run_does_not_edit(tmp_path: Path, capsys) -> None:
    vault = make_vault(tmp_path)
    source = write_page(vault, "references/source.md", body="Drop [[missing-page|Missing Label]].\n")
    original = source.read_text(encoding="utf-8")

    result = fixlink.run_fixlink(
        SimpleNamespace(
            print_prompt=False,
            no_codex=False,
            dry_run=True,
            remove_broken=True,
            json=True,
            no_log=True,
            codex_bin="codex",
            sandbox="workspace-write",
            add_dir=[],
        ),
        vault,
        {"OBSIDIAN_LINK_FORMAT": "wikilink"},
    )

    assert result == 0
    assert source.read_text(encoding="utf-8") == original
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "dry_run"
    assert report["summary"]["candidates"] == 1
    assert report["summary"]["links_removed"] == 0


def test_cli_rename_registers_fixlink_and_removes_cross_link() -> None:
    parser = cli.build_parser()

    args = parser.parse_args(["fixlink", "--dry-run"])
    assert args.alias == "fixlink"
    assert "wiki-fixlink" in cli.QMD_SYNC_SKILLS
    assert "cross-linker" not in cli.QMD_SYNC_SKILLS
    with pytest.raises(SystemExit):
        parser.parse_args(["cross-link"])

    remove_args = parser.parse_args(["fixlink", "--remove-broken", "--dry-run"])
    assert remove_args.alias == "fixlink"
    assert remove_args.remove_broken is True


def test_cli_fixlink_dispatch_routes_to_engine(tmp_path: Path, monkeypatch) -> None:
    vault = make_vault(tmp_path)
    calls: list[Path] = []

    def fake_run(args: object, vault_arg: Path, config: dict[str, str]) -> int:
        calls.append(vault_arg)
        return 0

    monkeypatch.chdir(vault)
    monkeypatch.setattr(fixlink, "run_fixlink", fake_run)

    result = cli.cmd_dispatch(
        SimpleNamespace(
            alias="fixlink",
            args=[],
            print_prompt=False,
            no_codex=True,
            dry_run=False,
            json=False,
            no_log=True,
            codex_bin="codex",
            sandbox="workspace-write",
            add_dir=[],
        )
    )

    assert result == 0
    assert calls == [vault]
