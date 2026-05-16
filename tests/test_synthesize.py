from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from a_inf import cli
from a_inf import lint
from a_inf import synthesize


def write_page(
    vault: Path,
    rel: str,
    *,
    title: str | None = None,
    tags: list[str] | None = None,
    summary: str | None = None,
    body: str = "",
    base_confidence: float = 0.5,
) -> Path:
    path = vault / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    page_title = title or path.stem.replace("-", " ").title()
    fm = [
        "---",
        f"title: {json.dumps(page_title)}",
        f"category: {json.dumps(rel.split('/', 1)[0])}",
        f"tags: {json.dumps(tags or ['systems'])}",
        "sources: [test]",
        f"summary: {json.dumps(summary if summary is not None else page_title + ' summary')}",
        "created: 2026-05-05T00:00:00+00:00",
        "updated: 2026-05-05T00:00:00+00:00",
        "lifecycle: draft",
        "lifecycle_changed: 2026-05-05",
        f"base_confidence: {base_confidence}",
        "---",
        "",
        body,
    ]
    path.write_text("\n".join(fm), encoding="utf-8")
    return path


def make_vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / ".manifest.json").write_text(
        '{"version": 1, "sources": {}, "projects": {}, "stats": {}}\n',
        encoding="utf-8",
    )
    (vault / "index.md").write_text("# Wiki Index\n", encoding="utf-8")
    (vault / "log.md").write_text("# Wiki Log\n", encoding="utf-8")
    (vault / "hot.md").write_text("# Hot Cache\n\n## Recent Activity\n- Existing.\n", encoding="utf-8")
    (vault / "_meta").mkdir()
    (vault / "_meta" / "taxonomy.md").write_text("# Taxonomy\n", encoding="utf-8")
    return vault


def add_raw_gap(vault: Path) -> None:
    write_page(vault, "concepts/a.md", title="Alpha", tags=["systems", "design"], body="Alpha body.")
    write_page(vault, "entities/b.md", title="Beta", tags=["systems"], body="Beta body.")
    for idx in range(3):
        write_page(
            vault,
            f"references/co-{idx}.md",
            title=f"Co {idx}",
            body="Uses [[concepts/a]] and [[entities/b]] together.",
        )


def synth_args(**overrides: object) -> SimpleNamespace:
    values = {
        "alias": "synthesize",
        "args": [],
        "print_prompt": False,
        "no_codex": True,
        "dry_run": False,
        "json": False,
        "no_log": True,
        "vscode": False,
        "vscode_bin": "code",
        "codex_bin": "codex",
        "sandbox": "workspace-write",
        "add_dir": [],
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_packet_uses_raw_lint_candidates(tmp_path: Path) -> None:
    vault = make_vault(tmp_path)
    add_raw_gap(vault)
    lint_packet = lint.build_lint_packet(vault, {})

    packet = synthesize.build_synthesis_packet(
        vault,
        {"OBSIDIAN_LINK_FORMAT": "wikilink"},
        lint_packet,
        lint.semantic_status("not_run", "one-hop", []),
        vault / "_runs" / "synthesis_plan.json",
        [],
    )

    assert packet["candidates"][0]["pair"] == ["concepts/a.md", "entities/b.md"]
    assert packet["candidates"][0]["target_path"] == "synthesis/alpha-x-beta.md"
    assert "concepts/a.md" in packet["authoring_context"]


def test_packet_prefers_fresh_semantic_review(tmp_path: Path) -> None:
    vault = make_vault(tmp_path)
    add_raw_gap(vault)
    run_dir = vault / "_runs" / "lint-20300101T000000Z"
    run_dir.mkdir(parents=True)
    packet = lint.build_lint_packet(vault, {})
    packet["generated_at"] = "2030-01-01T00:00:00+00:00"
    (run_dir / "packet.json").write_text(json.dumps(packet), encoding="utf-8")
    (run_dir / "semantic_review.json").write_text(
        json.dumps(
            {
                "status": "completed",
                "scope": "one-hop",
                "findings": {
                    "contradictions": [],
                    "synthesis_gaps": [
                        {
                            "candidate_id": "synthesis-gap-1",
                            "pair": ["concepts/a.md", "entities/b.md"],
                            "explanation": "A synthesis page would add a decision frame.",
                            "evidence_pages": ["references/co-0.md"],
                            "suggested_title": "Alpha × Beta",
                            "confidence": "high",
                        }
                    ],
                },
                "repair_recommendations": [],
                "reviewed_candidate_ids": ["synthesis-gap-1"],
                "warnings": [],
            }
        ),
        encoding="utf-8",
    )

    lint_packet, review, source = synthesize.load_candidate_context(vault, {})

    assert source.endswith("lint-20300101T000000Z")
    assert review["findings"]["synthesis_gaps"][0]["confidence"] == "high"
    assert lint_packet["generated_at"] == "2030-01-01T00:00:00+00:00"


def test_stale_lint_bundle_falls_back_to_inline_lint(tmp_path: Path) -> None:
    vault = make_vault(tmp_path)
    add_raw_gap(vault)
    run_dir = vault / "_runs" / "lint-20000101T000000Z"
    run_dir.mkdir(parents=True)
    packet = lint.build_lint_packet(vault, {})
    packet["generated_at"] = "2000-01-01T00:00:00+00:00"
    (run_dir / "packet.json").write_text(json.dumps(packet), encoding="utf-8")
    (run_dir / "semantic_review.json").write_text(
        json.dumps({**lint.semantic_status("completed", "one-hop", []), "findings": {"contradictions": [], "synthesis_gaps": []}}),
        encoding="utf-8",
    )

    _lint_packet, review, source = synthesize.load_candidate_context(vault, {})

    assert source == "inline_lint"
    assert review["status"] == "not_run"


def test_topic_filter_and_existing_synthesis_exclusion(tmp_path: Path) -> None:
    vault = make_vault(tmp_path)
    add_raw_gap(vault)
    write_page(
        vault,
        "synthesis/alpha-x-beta.md",
        title="Alpha × Beta",
        body="Synthesizes [[concepts/a]] and [[entities/b]].",
    )
    lint_packet = lint.build_lint_packet(vault, {})

    packet = synthesize.build_synthesis_packet(
        vault,
        {"OBSIDIAN_LINK_FORMAT": "wikilink"},
        lint_packet,
        lint.semantic_status("not_run", "one-hop", []),
        vault / "_runs" / "synthesis_plan.json",
        ["alpha"],
    )

    assert packet["candidates"] == []


def test_validate_rejects_unknown_and_deterministic_fields(tmp_path: Path) -> None:
    vault = make_vault(tmp_path)
    add_raw_gap(vault)
    packet = synthesize.build_synthesis_packet(
        vault,
        {"OBSIDIAN_LINK_FORMAT": "wikilink"},
        lint.build_lint_packet(vault, {}),
        lint.semantic_status("not_run", "one-hop", []),
        vault / "_runs" / "synthesis_plan.json",
        [],
    )

    with pytest.raises(synthesize.SynthesizeError):
        synthesize.validate_synthesis_plan(
            {"version": 1, "status": "completed", "decisions": [{"candidate_id": "missing", "action": "create"}]},
            packet,
            vault,
        )
    with pytest.raises(synthesize.SynthesizeError):
        synthesize.validate_synthesis_plan(
            {
                "version": 1,
                "status": "completed",
                "decisions": [
                    {
                        "candidate_id": packet["candidates"][0]["candidate_id"],
                        "action": "create",
                        "path": "synthesis/evil.md",
                        "summary": "Summary.",
                        "body": "Body.",
                    }
                ],
            },
            packet,
            vault,
        )


def test_apply_creates_page_backlinks_index_log_hot_and_manifest(tmp_path: Path) -> None:
    vault = make_vault(tmp_path)
    add_raw_gap(vault)
    config = {"OBSIDIAN_LINK_FORMAT": "markdown"}
    packet = synthesize.build_synthesis_packet(
        vault,
        config,
        lint.build_lint_packet(vault, {}),
        lint.semantic_status("not_run", "one-hop", []),
        vault / "_runs" / "synthesis_plan.json",
        [],
    )
    candidate_id = packet["candidates"][0]["candidate_id"]
    plan = synthesize.validate_synthesis_plan(
        {
            "version": 1,
            "status": "completed",
            "decisions": [
                {
                    "candidate_id": candidate_id,
                    "action": "create",
                    "summary": "How Alpha and Beta interact.",
                    "body": "## The Connection\n\nAlpha and Beta interact in the shared workflow. ^[inferred]",
                    "open_questions": ["When does Beta constrain Alpha?"],
                }
            ],
            "warnings": [],
        },
        packet,
        vault,
    )

    applied = synthesize.apply_synthesis_plan(plan, vault, config)
    synthesize.append_log(vault, packet, applied)
    synthesize.write_hot(vault, plan, applied)
    synthesize.update_manifest_stats(vault)

    path = vault / "synthesis/alpha-x-beta.md"
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert "title: \"Alpha × Beta\"" in text
    assert "[Alpha × Beta](../synthesis/alpha-x-beta.md) - synthesis" in (vault / "concepts/a.md").read_text(encoding="utf-8")
    assert "[Alpha × Beta](../synthesis/alpha-x-beta.md) - synthesis" in (vault / "entities/b.md").read_text(encoding="utf-8")
    assert "Alpha × Beta" in (vault / "index.md").read_text(encoding="utf-8")
    assert "WIKI_SYNTHESIZE" in (vault / "log.md").read_text(encoding="utf-8")
    assert "When does Beta constrain Alpha?" in (vault / "hot.md").read_text(encoding="utf-8")
    manifest = json.loads((vault / ".manifest.json").read_text(encoding="utf-8"))
    assert manifest["stats"]["total_pages"] >= 6


def test_run_synthesize_dry_run_validates_without_editing(tmp_path: Path, monkeypatch, capsys) -> None:
    vault = make_vault(tmp_path)
    add_raw_gap(vault)

    def fake_call(command: list[str], cwd: Path, env: dict[str, str] | None = None) -> int:
        prompt = command[-1]
        packet_path = Path(prompt.split("Deterministic packet path: ", 1)[1].splitlines()[0])
        plan_path = Path(prompt.split("Write synthesis plan JSON to: ", 1)[1].splitlines()[0])
        packet = json.loads(packet_path.read_text(encoding="utf-8"))
        plan_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "status": "completed",
                    "decisions": [
                        {
                            "candidate_id": packet["candidates"][0]["candidate_id"],
                            "action": "create",
                            "summary": "Dry run summary.",
                            "body": "Dry run body.",
                        }
                    ],
                    "warnings": [],
                }
            ),
            encoding="utf-8",
        )
        return 0

    monkeypatch.setattr(synthesize.shutil, "which", lambda _: "/usr/local/bin/codex")
    monkeypatch.setattr(subprocess, "call", fake_call)

    result = synthesize.run_synthesize(
        synth_args(no_codex=False, dry_run=True, json=True),
        vault,
        {"OBSIDIAN_LINK_FORMAT": "wikilink"},
    )

    assert result == 0
    assert not (vault / "synthesis/alpha-x-beta.md").exists()
    assert json.loads(capsys.readouterr().out)["status"] == "dry_run"


def test_run_synthesize_vscode_opens_created_pages(tmp_path: Path, monkeypatch) -> None:
    vault = make_vault(tmp_path)
    add_raw_gap(vault)
    opened: list[list[str]] = []

    def fake_call(command: list[str], cwd: Path, env: dict[str, str] | None = None) -> int:
        prompt = command[-1]
        packet_path = Path(prompt.split("Deterministic packet path: ", 1)[1].splitlines()[0])
        plan_path = Path(prompt.split("Write synthesis plan JSON to: ", 1)[1].splitlines()[0])
        packet = json.loads(packet_path.read_text(encoding="utf-8"))
        plan_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "status": "completed",
                    "decisions": [
                        {
                            "candidate_id": packet["candidates"][0]["candidate_id"],
                            "action": "create",
                            "summary": "Created summary.",
                            "body": "Created body.",
                        }
                    ],
                    "warnings": [],
                }
            ),
            encoding="utf-8",
        )
        return 0

    monkeypatch.setattr(synthesize.shutil, "which", lambda name: f"/usr/local/bin/{name}")
    monkeypatch.setattr(synthesize.subprocess, "call", fake_call)
    monkeypatch.setattr(
        synthesize.subprocess,
        "run",
        lambda command, **_kwargs: opened.append(command) or subprocess.CompletedProcess(command, 0),
    )
    monkeypatch.setattr(synthesize, "ensure_qmd_collection", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(synthesize, "sync_qmd", lambda *_args, **_kwargs: True)

    result = synthesize.run_synthesize(
        synth_args(no_codex=False, vscode=True),
        vault,
        {"OBSIDIAN_LINK_FORMAT": "wikilink"},
    )

    created = vault / "synthesis/alpha-x-beta.md"
    assert result == 0
    assert created.exists()
    assert opened == [["/usr/local/bin/code", str(created)]]


def test_cli_synthesize_dispatch_routes_to_engine(tmp_path: Path, monkeypatch) -> None:
    vault = make_vault(tmp_path)
    calls: list[Path] = []

    def fake_run(args: object, vault_arg: Path, config: dict[str, str]) -> int:
        calls.append(vault_arg)
        return 0

    monkeypatch.chdir(vault)
    monkeypatch.setattr(synthesize, "run_synthesize", fake_run)

    result = cli.cmd_dispatch(synth_args(no_log=True))

    assert result == 0
    assert calls == [vault]


def test_cli_parser_accepts_synthesize_flags() -> None:
    parser = cli.build_parser()

    args = parser.parse_args(["synthesize", "--dry-run", "--json", "--no-log", "--vscode", "--vscode-bin", "code-insiders", "alpha"])

    assert args.alias == "synthesize"
    assert args.dry_run is True
    assert args.json is True
    assert args.no_log is True
    assert args.vscode is True
    assert args.vscode_bin == "code-insiders"
    assert args.args == ["alpha"]
