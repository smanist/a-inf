from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from a_inf import cli, ingest


class IngestArgs:
    alias = "ingest"
    data = False
    mode = "append"
    full = False
    raw = False
    print_prompt = False
    no_codex = False
    codex_bin = "codex"
    sandbox = "workspace-write"
    add_dir: list[str] = []

    def __init__(self, args: list[str]) -> None:
        self.args = args


def make_vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    for dirname in ["concepts", "entities", "skills", "references", "synthesis", "journal", "projects", "_raw"]:
        (vault / dirname).mkdir(parents=True)
    (vault / ".manifest.json").write_text(json.dumps({"version": 1, "sources": {}, "projects": {}, "stats": {}}), encoding="utf-8")
    (vault / "index.md").write_text("---\ntitle: Wiki Index\n---\n\n# Wiki Index\n", encoding="utf-8")
    (vault / "log.md").write_text("---\ntitle: Wiki Log\n---\n\n# Wiki Log\n", encoding="utf-8")
    (vault / "hot.md").write_text("---\ntitle: Hot Cache\n---\n\n# Hot Cache\n", encoding="utf-8")
    return vault


def page_plan(source: Path, *, action: str = "create", path: str = "concepts/deterministic-ingest.md") -> dict[str, object]:
    digest = ingest.hash_file(source)
    now = "2026-05-05T12:00:00+00:00"
    return {
        "version": 1,
        "mode": "append",
        "sources": [
            {
                "path": str(source),
                "manifest_key": str(source),
                "source_type": "document",
                "content_hash": digest,
                "project": None,
                "pages_created": [path] if action == "create" else [],
                "pages_updated": [path] if action == "update" else [],
            }
        ],
        "pages": [
            {
                "action": action,
                "path": path,
                "frontmatter": {
                    "title": "Deterministic Ingest",
                    "category": "concepts",
                    "tags": ["ingest"],
                    "sources": [str(source)],
                    "summary": "A deterministic shell around semantic wiki ingest.",
                    "provenance": {"extracted": 1.0, "inferred": 0.0, "ambiguous": 0.0},
                    "base_confidence": 0.4,
                    "lifecycle": "draft",
                    "lifecycle_changed": "2026-05-05",
                    "created": now,
                    "updated": now,
                },
                "body": "# Deterministic Ingest\n\nHybrid ingest separates extraction from apply.",
                "links": [],
                "source_refs": [str(source)],
            }
        ],
        "hot_update": {
            "recent_activity": ["Ingested deterministic ingest notes."],
            "active_threads": ["Hybrid ingest engine"],
            "key_takeaways": ["Apply is deterministic."],
            "flagged_contradictions": [],
        },
        "warnings": [],
    }


def test_ingest_print_prompt_emits_packet_without_writes(tmp_path: Path, monkeypatch, capsys) -> None:
    vault = make_vault(tmp_path)
    source = vault / "note.md"
    source.write_text("hybrid ingest\n", encoding="utf-8")
    args = IngestArgs([str(source)])
    args.print_prompt = True

    monkeypatch.chdir(vault)
    monkeypatch.setattr(subprocess, "call", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("codex called")))

    assert cli.cmd_dispatch(args) == 0
    packet = json.loads(capsys.readouterr().out)
    assert packet["mode"] == "append"
    assert packet["sources"][0]["content_hash"] == ingest.hash_file(source)
    assert "codex_prompt" in packet
    assert not (vault / ".a-inf" / "runs").exists()


def test_ingest_valid_plan_applies_pages_and_special_files(tmp_path: Path, monkeypatch) -> None:
    vault = make_vault(tmp_path)
    source = vault / "note.md"
    source.write_text("hybrid ingest\n", encoding="utf-8")
    args = IngestArgs([str(source)])

    def fake_call(command: list[str], cwd: Path) -> int:
        match = re.search(r"Write exactly one JSON file at this path: (.+)", command[-1])
        assert match
        plan_path = Path(match.group(1).strip())
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        plan_path.write_text(json.dumps(page_plan(source)), encoding="utf-8")
        return 0

    monkeypatch.chdir(vault)
    monkeypatch.setattr(ingest.shutil, "which", lambda _: "/usr/local/bin/codex")
    monkeypatch.setattr(ingest.subprocess, "call", fake_call)

    assert cli.cmd_dispatch(args) == 0
    page = vault / "concepts" / "deterministic-ingest.md"
    assert page.is_file()
    manifest = json.loads((vault / ".manifest.json").read_text(encoding="utf-8"))
    assert str(source) in manifest["sources"]
    assert manifest["sources"][str(source)]["pages_created"] == ["concepts/deterministic-ingest.md"]
    index = (vault / "index.md").read_text(encoding="utf-8")
    assert "[[concepts/deterministic-ingest|Deterministic Ingest]]" in index
    assert " - A deterministic shell around semantic wiki ingest." in index
    assert "INGEST" in (vault / "log.md").read_text(encoding="utf-8")
    assert "Apply is deterministic." in (vault / "hot.md").read_text(encoding="utf-8")


def test_ingest_invalid_plan_fails_without_wiki_writes(tmp_path: Path, monkeypatch) -> None:
    vault = make_vault(tmp_path)
    source = vault / "note.md"
    source.write_text("hybrid ingest\n", encoding="utf-8")
    original_manifest = (vault / ".manifest.json").read_text(encoding="utf-8")
    args = IngestArgs([str(source)])

    def fake_call(command: list[str], cwd: Path) -> int:
        match = re.search(r"Write exactly one JSON file at this path: (.+)", command[-1])
        assert match
        plan_path = Path(match.group(1).strip())
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        plan_path.write_text(json.dumps({"version": 1, "mode": "append", "sources": [], "pages": []}), encoding="utf-8")
        return 0

    monkeypatch.chdir(vault)
    monkeypatch.setattr(ingest.shutil, "which", lambda _: "/usr/local/bin/codex")
    monkeypatch.setattr(ingest.subprocess, "call", fake_call)

    assert cli.cmd_dispatch(args) == 1
    assert not (vault / "concepts" / "deterministic-ingest.md").exists()
    assert (vault / ".manifest.json").read_text(encoding="utf-8") == original_manifest


def test_append_mode_skips_unchanged_sources_in_packet(tmp_path: Path, monkeypatch, capsys) -> None:
    vault = make_vault(tmp_path)
    source = vault / "note.md"
    source.write_text("hybrid ingest\n", encoding="utf-8")
    digest = ingest.hash_file(source)
    modified = ingest.format_datetime(ingest.datetime.fromtimestamp(source.stat().st_mtime, ingest.timezone.utc))
    manifest = {
        "version": 1,
        "sources": {
            str(source): {
                "ingested_at": modified,
                "modified_at": modified,
                "content_hash": digest,
                "size_bytes": source.stat().st_size,
                "source_type": "document",
            }
        },
        "projects": {},
        "stats": {},
    }
    (vault / ".manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    args = IngestArgs([str(source)])
    args.print_prompt = True

    monkeypatch.chdir(vault)

    assert cli.cmd_dispatch(args) == 0
    packet = json.loads(capsys.readouterr().out)
    assert packet["sources"] == []


def test_url_and_data_ingest_keep_dispatch_path(tmp_path: Path, monkeypatch) -> None:
    vault = make_vault(tmp_path)
    calls: list[str] = []

    def fake_run_dispatch(dispatch: cli.Dispatch, args: object) -> int:
        calls.append(dispatch.skill)
        return 0

    monkeypatch.chdir(vault)
    monkeypatch.setattr(cli, "run_dispatch", fake_run_dispatch)

    assert cli.cmd_dispatch(IngestArgs(["https://example.com/article"])) == 0
    data_args = IngestArgs(["export.json"])
    data_args.data = True
    assert cli.cmd_dispatch(data_args) == 0
    assert calls == ["ingest-url", "data-ingest"]


def test_raw_delete_outside_raw_dir_is_rejected(tmp_path: Path) -> None:
    vault = make_vault(tmp_path)
    raw = vault / "_raw" / "draft.md"
    raw.write_text("draft\n", encoding="utf-8")
    source = ingest.build_source(raw, {"sources": {}})
    plan = page_plan(raw)
    plan["mode"] = "raw"
    plan["raw_files_to_delete"] = [str(vault / "note.md")]
    plan["sources"][0]["manifest_key"] = str(raw)
    plan["sources"][0]["path"] = str(raw)
    plan["pages"][0]["source_refs"] = [str(raw)]

    try:
        ingest.validate_plan(plan, vault, {"OBSIDIAN_LINK_FORMAT": "wikilink"}, {"sources": {}}, [source], "raw")
    except ingest.IngestError as exc:
        assert "outside raw dir" in str(exc)
    else:
        raise AssertionError("expected raw delete validation failure")


def test_duplicate_page_operations_are_rejected(tmp_path: Path) -> None:
    vault = make_vault(tmp_path)
    source = vault / "note.md"
    source.write_text("hybrid ingest\n", encoding="utf-8")
    source_entry = ingest.build_source(source, {"sources": {}})
    plan = page_plan(source)
    plan["pages"].append(dict(plan["pages"][0]))

    try:
        ingest.validate_plan(
            plan,
            vault,
            {"OBSIDIAN_LINK_FORMAT": "wikilink"},
            {"sources": {}},
            [source_entry],
            "append",
        )
    except ingest.IngestError as exc:
        assert "Duplicate page operation" in str(exc)
    else:
        raise AssertionError("expected duplicate page validation failure")


def test_update_must_preserve_existing_lifecycle(tmp_path: Path) -> None:
    vault = make_vault(tmp_path)
    existing = vault / "concepts" / "deterministic-ingest.md"
    existing.write_text(
        """---
title: Deterministic Ingest
category: concepts
tags: ["ingest"]
sources: ["old.md"]
summary: "Existing reviewed page."
provenance:
  extracted: 1.0
  inferred: 0.0
  ambiguous: 0.0
base_confidence: 0.8
lifecycle: verified
lifecycle_changed: "2026-05-01"
created: "2026-05-01T00:00:00+00:00"
updated: "2026-05-01T00:00:00+00:00"
---

# Deterministic Ingest
""",
        encoding="utf-8",
    )
    source = vault / "note.md"
    source.write_text("hybrid ingest\n", encoding="utf-8")
    source_entry = ingest.build_source(source, {"sources": {}})
    plan = page_plan(source, action="update")

    try:
        ingest.validate_plan(
            plan,
            vault,
            {"OBSIDIAN_LINK_FORMAT": "wikilink"},
            {"sources": {}},
            [source_entry],
            "append",
        )
    except ingest.IngestError as exc:
        assert "preserve existing lifecycle" in str(exc)
    else:
        raise AssertionError("expected lifecycle preservation validation failure")

    plan["pages"][0]["frontmatter"]["lifecycle"] = "verified"
    plan["pages"][0]["frontmatter"]["lifecycle_changed"] = "2026-05-01"
    validated = ingest.validate_plan(
        plan,
        vault,
        {"OBSIDIAN_LINK_FORMAT": "wikilink"},
        {"sources": {}},
        [source_entry],
        "append",
    )
    assert validated.pages[0]["frontmatter"]["lifecycle"] == "verified"


def test_prune_runs_keeps_latest_twenty(tmp_path: Path) -> None:
    vault = make_vault(tmp_path)
    root = vault / ".a-inf" / "runs"
    for index in range(25):
        (root / f"20260505T0000{index:02d}Z").mkdir(parents=True)

    ingest.prune_runs(vault)

    assert len([path for path in root.iterdir() if path.is_dir()]) == 20
