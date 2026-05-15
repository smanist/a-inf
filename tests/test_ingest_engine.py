from __future__ import annotations

import json
import re
import subprocess
import base64
from pathlib import Path

from a_inf import cli, ingest
from a_inf import qmd as qmd_module


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


def fake_qmd_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
    if command[-1] == "--version":
        return subprocess.CompletedProcess(command, 0, stdout="qmd 2.1.0\n", stderr="")
    if command[1:] == ["collection", "show", "vault"]:
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
    if command[1:] in (["update"], ["embed"]):
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
    raise AssertionError(f"unexpected qmd command: {command}")


def plan_path_from_prompt(prompt: str) -> Path:
    match = re.search(r"Write exactly one JSON file at this path: (.+)", prompt)
    assert match
    return Path(match.group(1).strip())


def test_call_codex_exec_passes_prompt_on_stdin(tmp_path: Path, monkeypatch) -> None:
    seen: dict[str, object] = {}

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        seen["command"] = command
        seen["kwargs"] = kwargs
        return subprocess.CompletedProcess(command, 17, stdout="", stderr="")

    monkeypatch.setattr(ingest.subprocess, "run", fake_run)

    result = ingest.call_codex_exec(["/usr/local/bin/codex", "exec"], "large prompt", cwd=tmp_path, env={"A": "B"})

    assert result == 17
    assert seen["command"] == ["/usr/local/bin/codex", "exec", "-"]
    assert seen["kwargs"] == {"cwd": tmp_path, "env": {"A": "B"}, "input": "large prompt", "text": True, "check": False}


def isolate_qmd_home(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "qmd-home"
    monkeypatch.setattr(qmd_module.Path, "home", classmethod(lambda cls: home))


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


def url_plan(
    url: str,
    markdown: str,
    *,
    action: str = "create",
    path: str = "references/web-example-com-article.md",
    content_hash: str | None = None,
) -> dict[str, object]:
    digest = content_hash or ingest.hash_bytes(markdown.encode("utf-8"))
    now = "2026-05-05T12:00:00+00:00"
    return {
        "version": 1,
        "mode": "append",
        "sources": [
            {
                "path": url,
                "manifest_key": url,
                "source_type": "url",
                "source_url": url,
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
                    "title": "Example Article",
                    "category": "references",
                    "tags": ["web"],
                    "sources": [url],
                    "source_url": url,
                    "summary": "A URL reference extracted through defuddle.",
                    "provenance": {"extracted": 1.0, "inferred": 0.0, "ambiguous": 0.0},
                    "base_confidence": 0.4,
                    "lifecycle": "draft",
                    "lifecycle_changed": "2026-05-05",
                    "created": now,
                    "updated": now,
                },
                "body": "# Example Article\n\nDefuddle provided markdown for planning.",
                "links": [],
                "source_refs": [url],
            }
        ],
        "hot_update": {
            "recent_activity": ["Ingested Example Article from the web."],
            "active_threads": [],
            "key_takeaways": ["URL ingest uses deterministic extraction."],
            "flagged_contradictions": [],
        },
        "warnings": [],
    }


def fake_run_with_defuddle(markdown: str) -> object:
    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if command[-1] == "--version":
            return subprocess.CompletedProcess(command, 0, stdout="qmd 2.1.0\n", stderr="")
        if command[1:] == ["collection", "show", "vault"]:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if command[1:] in (["update"], ["embed"]):
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if command[:2] == ["/usr/local/bin/defuddle", "parse"] and command[-1] == "--md":
            return subprocess.CompletedProcess(command, 0, stdout=markdown, stderr="")
        raise AssertionError(f"unexpected command: {command}")

    return fake_run


def fake_fetch_url_html(html: str) -> object:
    def fake_fetch(_url: str) -> bytes:
        return html.encode("utf-8")

    return fake_fetch


def fake_run_with_mineru(markdown: str, content_list: list[dict[str, object]] | None = None) -> object:
    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if command[-1] == "--version" and Path(command[0]).name == "qmd":
            return subprocess.CompletedProcess(command, 0, stdout="qmd 2.1.0\n", stderr="")
        if command == ["/usr/local/bin/mineru", "--version"]:
            return subprocess.CompletedProcess(command, 0, stdout="mineru, version 2.7.6\n", stderr="")
        if Path(command[0]).name == "qmd" and command[1:] == ["collection", "show", "vault"]:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if Path(command[0]).name == "qmd" and command[1:] in (["update"], ["embed"]):
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if command and command[0] == "/usr/local/bin/mineru":
            source = Path(command[command.index("-p") + 1])
            output = Path(command[command.index("-o") + 1])
            method = command[command.index("-m") + 1] if "-m" in command else "auto"
            extract_dir = output / source.stem / method
            extract_dir.mkdir(parents=True, exist_ok=True)
            (extract_dir / f"{source.stem}.md").write_text(markdown, encoding="utf-8")
            (extract_dir / f"{source.stem}_content_list.json").write_text(
                json.dumps(content_list or [{"type": "text", "text": markdown, "page_idx": 0}]),
                encoding="utf-8",
            )
            for item in content_list or []:
                img_path = item.get("img_path")
                if isinstance(img_path, str):
                    target = extract_dir / img_path
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(b"fake image")
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        raise AssertionError(f"unexpected command: {command}")

    return fake_run


def test_ingest_print_prompt_emits_packet_without_writes(tmp_path: Path, monkeypatch, capsys) -> None:
    isolate_qmd_home(tmp_path, monkeypatch)
    vault = make_vault(tmp_path)
    configured_sources = tmp_path / "configured-sources"
    configured_sources.mkdir()
    (configured_sources / "note.md").write_text("do not include during explicit URL ingest\n", encoding="utf-8")
    (vault / ".env").write_text(
        f"QMD_WIKI_COLLECTION=vault\nQMD_PAPERS_COLLECTION=vault\nOBSIDIAN_SOURCES_DIR={configured_sources}\n",
        encoding="utf-8",
    )
    source = vault / "note.md"
    source.write_text("hybrid ingest\n", encoding="utf-8")
    args = IngestArgs([str(source)])
    args.print_prompt = True

    monkeypatch.chdir(vault)
    monkeypatch.setattr(qmd_module.shutil, "which", lambda name: "/usr/local/bin/qmd" if name == "qmd" else None)
    monkeypatch.setattr(qmd_module.subprocess, "run", fake_qmd_run)
    monkeypatch.setattr(ingest, "call_codex_exec", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("codex called")))

    assert cli.cmd_dispatch(args) == 0
    packet = json.loads(capsys.readouterr().out)
    assert packet["mode"] == "append"
    assert packet["qmd"] == {
        "binary": "/usr/local/bin/qmd",
        "version": "qmd 2.1.0",
        "wiki_collection": "vault",
        "papers_collection": "vault",
        "index_path": str(vault / ".a-inf" / "qmd" / "index.sqlite"),
        "INDEX_PATH": str(vault / ".a-inf" / "qmd" / "index.sqlite"),
        "XDG_CACHE_HOME": str(vault / ".a-inf" / "qmd" / "cache"),
        "XDG_CONFIG_HOME": str(vault / ".a-inf" / "qmd" / "config"),
        "lookup_policy": "Use qmd search --json -n 5 only. Do not use qmd query, vsearch, reranking, or model-backed QMD commands during ingest.",
    }
    assert packet["qmd_wiki_collection"] == "vault"
    assert packet["qmd_papers_collection"] == "vault"
    assert packet["sources"][0]["content_hash"] == ingest.hash_file(source)
    assert "codex_prompt" in packet
    assert "wiki-ingest skill instructions:" in packet["codex_prompt"]
    assert "# Obsidian Ingest Planner" in packet["codex_prompt"]
    assert "do not run `qmd query`" in packet["codex_prompt"]
    assert "qmd search --json -n 5" in packet["codex_prompt"]
    assert not (vault / "_runs").exists()


def test_html_extract_extracts_title_headings_sections_and_visible_text() -> None:
    html = """
    <!doctype html>
    <html>
      <head>
        <title>Adjuster Notes</title>
        <style>.hidden { color: red; }</style>
        <script>window.secret = "skip me";</script>
      </head>
      <body>
        <h1>Claim Adjustment</h1>
        <p>Durable visible text.</p>
        <h2>Reserve Review</h2>
        <noscript>Do not include fallback text.</noscript>
      </body>
    </html>
    """

    extract = ingest.parse_html_extract(html)

    assert extract["title"] == "Adjuster Notes"
    assert extract["headings"] == [
        {"tag": "h1", "text": "Claim Adjustment"},
        {"tag": "h2", "text": "Reserve Review"},
    ]
    assert extract["sections"][0]["heading"] == "Claim Adjustment"
    assert "Durable visible text." in extract["sections"][0]["text"]
    assert "Durable visible text." in extract["text_sample"]
    assert "skip me" not in extract["text_sample"]
    assert "fallback text" not in extract["text_sample"]


def test_html_extract_captures_later_sections() -> None:
    middle = "\n".join(f"<p>Filler {index}</p>" for index in range(100))
    html = f"""
    <html>
      <head><title>Long Notes</title></head>
      <body>
        <h2>Early</h2>
        <p>Early detail.</p>
        {middle}
        <h2>Late</h2>
        <p>Important late detail.</p>
      </body>
    </html>
    """

    extract = ingest.parse_html_extract(html)

    headings = [heading["text"] for heading in extract["headings"]]
    assert headings == ["Early", "Late"]
    late = [section for section in extract["sections"] if section["heading"] == "Late"][0]
    assert "Important late detail." in late["text"]


def test_local_html_uses_defuddle_markdown_and_embedded_figures(tmp_path: Path, monkeypatch, capsys) -> None:
    isolate_qmd_home(tmp_path, monkeypatch)
    vault = make_vault(tmp_path)
    source = vault / "_raw" / "adj.html"
    image = base64.b64encode(b"fake png").decode("ascii")
    source.write_text(
        f'<html><body><h1>Claim</h1><img alt="plot" src="data:image/png;base64,{image}"><p>Notes</p></body></html>',
        encoding="utf-8",
    )
    args = IngestArgs([str(source)])
    args.print_prompt = True

    monkeypatch.chdir(vault)
    monkeypatch.setattr(ingest.shutil, "which", lambda name: f"/usr/local/bin/{name}" if name in {"defuddle", "qmd"} else None)
    monkeypatch.setattr(qmd_module.shutil, "which", lambda name: f"/usr/local/bin/{name}" if name in {"defuddle", "qmd"} else None)
    monkeypatch.setattr(ingest.subprocess, "run", fake_run_with_defuddle("# Claim\n\n![plot](_sources/archive/figures/figure-001.png)\n\nNotes"))

    assert cli.cmd_dispatch(args) == 0
    packet = json.loads(capsys.readouterr().out)
    packet_source = packet["sources"][0]
    assert packet_source["html_markdown"].startswith("# Claim")
    assert "data:image" not in packet_source["html_markdown"]
    assert packet_source["embedded_figures"][0]["mime_type"] == "image/png"
    assert packet_source["embedded_figures"][0]["size_bytes"] == len(b"fake png")
    assert "html_extract" not in packet_source


def test_url_source_packet_uses_defuddle_markdown(tmp_path: Path, monkeypatch, capsys) -> None:
    isolate_qmd_home(tmp_path, monkeypatch)
    vault = make_vault(tmp_path)
    (vault / ".env").write_text("QMD_WIKI_COLLECTION=vault\nQMD_PAPERS_COLLECTION=vault\n", encoding="utf-8")
    url = "https://example.com/article"
    markdown = "# Example Article\n\nFetched body."
    args = IngestArgs([url])
    args.print_prompt = True

    monkeypatch.chdir(vault)
    monkeypatch.setattr(ingest.shutil, "which", lambda name: f"/usr/local/bin/{name}" if name in {"defuddle", "qmd"} else None)
    monkeypatch.setattr(qmd_module.shutil, "which", lambda name: f"/usr/local/bin/{name}" if name in {"defuddle", "qmd"} else None)
    monkeypatch.setattr(ingest, "fetch_url_html", fake_fetch_url_html(markdown))
    monkeypatch.setattr(ingest.subprocess, "run", fake_run_with_defuddle(markdown))
    monkeypatch.setattr(ingest, "call_codex_exec", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("codex called")))

    assert cli.cmd_dispatch(args) == 0
    packet = json.loads(capsys.readouterr().out)
    assert len(packet["sources"]) == 1
    source = packet["sources"][0]
    assert source["path"] == url
    assert source["manifest_key"] == url
    assert source["source_type"] == "url"
    assert source["source_url"] == url
    assert source["html_markdown"] == markdown
    assert source["target_path"] == "references/web-example-com-article.md"
    assert source["content_hash"] == ingest.hash_bytes(markdown.encode("utf-8"))
    assert "html_markdown" in packet["codex_prompt"]
    assert not (vault / "_runs").exists()


def test_url_source_packet_extracts_embedded_figures(tmp_path: Path, monkeypatch, capsys) -> None:
    isolate_qmd_home(tmp_path, monkeypatch)
    vault = make_vault(tmp_path)
    url = "https://example.com/slides.html"
    image = base64.b64encode(b"fake png").decode("ascii")
    html = f'<html><body><img src="data:image/png;base64,{image}"><img src="data:image/png;base64,{image}"></body></html>'
    markdown = "# Slides\n\n![](_sources/archive/figures/figure-001.png)"
    args = IngestArgs([url])
    args.print_prompt = True

    monkeypatch.chdir(vault)
    monkeypatch.setattr(ingest.shutil, "which", lambda name: f"/usr/local/bin/{name}" if name in {"defuddle", "qmd"} else None)
    monkeypatch.setattr(qmd_module.shutil, "which", lambda name: f"/usr/local/bin/{name}" if name in {"defuddle", "qmd"} else None)
    monkeypatch.setattr(ingest, "fetch_url_html", fake_fetch_url_html(html))
    monkeypatch.setattr(ingest.subprocess, "run", fake_run_with_defuddle(markdown))

    assert cli.cmd_dispatch(args) == 0
    packet = json.loads(capsys.readouterr().out)
    source = packet["sources"][0]
    assert source["html_markdown"] == markdown
    assert "data:image" not in source["html_markdown"]
    assert len(source["embedded_figures"]) == 1
    assert source["embedded_figures"][0]["filename"] == "figure-001.png"


def test_url_source_packet_warns_on_invalid_embedded_figure(tmp_path: Path, monkeypatch, capsys) -> None:
    isolate_qmd_home(tmp_path, monkeypatch)
    vault = make_vault(tmp_path)
    url = "https://example.com/bad-figure.html"
    html = '<html><body><img src="data:image/png;base64,%%%"></body></html>'
    markdown = "# Bad Figure"
    args = IngestArgs([url])
    args.print_prompt = True

    monkeypatch.chdir(vault)
    monkeypatch.setattr(ingest.shutil, "which", lambda name: f"/usr/local/bin/{name}" if name in {"defuddle", "qmd"} else None)
    monkeypatch.setattr(qmd_module.shutil, "which", lambda name: f"/usr/local/bin/{name}" if name in {"defuddle", "qmd"} else None)
    monkeypatch.setattr(ingest, "fetch_url_html", fake_fetch_url_html(html))
    monkeypatch.setattr(ingest.subprocess, "run", fake_run_with_defuddle(markdown))

    assert cli.cmd_dispatch(args) == 0
    packet = json.loads(capsys.readouterr().out)
    source = packet["sources"][0]
    assert source["embedded_figures"] == []
    assert "omitted invalid embedded image data URI" in source["html_extraction"]["warnings"][0]


def test_oversized_sanitized_prompt_fails_before_codex(tmp_path: Path, monkeypatch, capsys) -> None:
    isolate_qmd_home(tmp_path, monkeypatch)
    vault = make_vault(tmp_path)
    url = "https://example.com/large.html"
    args = IngestArgs([url])

    monkeypatch.chdir(vault)
    monkeypatch.setattr(ingest, "CODEX_PROMPT_MAX_CHARS", 1_000)
    monkeypatch.setattr(ingest.shutil, "which", lambda name: f"/usr/local/bin/{name}" if name in {"defuddle", "qmd", "codex"} else None)
    monkeypatch.setattr(qmd_module.shutil, "which", lambda name: f"/usr/local/bin/{name}" if name in {"defuddle", "qmd", "codex"} else None)
    monkeypatch.setattr(ingest, "fetch_url_html", fake_fetch_url_html("<html><body>Large</body></html>"))
    monkeypatch.setattr(ingest.subprocess, "run", fake_run_with_defuddle("# Large\n\n" + ("body\n" * 1_000)))
    monkeypatch.setattr(ingest, "call_codex_exec", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("codex called")))

    assert cli.cmd_dispatch(args) == 1
    err = capsys.readouterr().err
    assert "above the 1000 character safety budget" in err
    assert "html_markdown" in err


def test_url_ingest_fails_before_planning_when_defuddle_is_missing(tmp_path: Path, monkeypatch) -> None:
    isolate_qmd_home(tmp_path, monkeypatch)
    vault = make_vault(tmp_path)
    args = IngestArgs(["https://example.com/article"])

    monkeypatch.chdir(vault)
    monkeypatch.setattr(ingest.shutil, "which", lambda name: None if name == "defuddle" else f"/usr/local/bin/{name}")
    monkeypatch.setattr(ingest, "fetch_url_html", fake_fetch_url_html("<html></html>"))
    monkeypatch.setattr(ingest.subprocess, "run", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("subprocess.run called")))
    monkeypatch.setattr(ingest, "call_codex_exec", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("codex called")))

    assert cli.cmd_dispatch(args) == 1
    assert not (vault / "_runs").exists()


def test_url_ingest_fails_before_planning_when_defuddle_fails_or_empty(tmp_path: Path, monkeypatch) -> None:
    isolate_qmd_home(tmp_path, monkeypatch)
    vault = make_vault(tmp_path)
    args = IngestArgs(["https://example.com/article"])

    monkeypatch.chdir(vault)
    monkeypatch.setattr(ingest.shutil, "which", lambda name: f"/usr/local/bin/{name}")
    monkeypatch.setattr(ingest, "fetch_url_html", fake_fetch_url_html("<html></html>"))
    monkeypatch.setattr(ingest, "call_codex_exec", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("codex called")))
    monkeypatch.setattr(
        ingest.subprocess,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(command, 2, stdout="", stderr="network failed"),
    )

    assert cli.cmd_dispatch(args) == 1
    assert not (vault / "_runs").exists()

    monkeypatch.setattr(
        ingest.subprocess,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(command, 0, stdout="  \n", stderr=""),
    )

    assert cli.cmd_dispatch(args) == 1
    assert not (vault / "_runs").exists()


def test_pdf_source_packet_uses_mineru_markdown(tmp_path: Path, monkeypatch, capsys) -> None:
    isolate_qmd_home(tmp_path, monkeypatch)
    vault = make_vault(tmp_path)
    (vault / ".env").write_text(
        "QMD_WIKI_COLLECTION=vault\nQMD_PAPERS_COLLECTION=vault\nA_INF_PDF_EXTRACTOR=mineru\n",
        encoding="utf-8",
    )
    source = vault / "_raw" / "paper.pdf"
    source.write_bytes(b"%PDF fake test source")
    markdown = "# Paper Title\n\nMinerU extracted PDF body."
    args = IngestArgs([str(source)])
    args.print_prompt = True

    monkeypatch.chdir(vault)
    monkeypatch.setattr(ingest.shutil, "which", lambda name: f"/usr/local/bin/{name}" if name in {"mineru", "qmd"} else None)
    monkeypatch.setattr(
        ingest.subprocess,
        "run",
        fake_run_with_mineru(markdown, [{"type": "text", "text": "Paper Title", "page_idx": 0}]),
    )

    assert cli.cmd_dispatch(args) == 0
    packet = json.loads(capsys.readouterr().out)
    source_packet = packet["sources"][0]
    assert source_packet["source_type"] == "pdf"
    assert source_packet["content_hash"] == ingest.hash_file(source)
    extract = source_packet["pdf_extract"]
    assert extract["status"] == "extracted"
    assert extract["extractor"] == "mineru"
    assert extract["version"] == "mineru, version 2.7.6"
    assert extract["markdown"] == markdown
    assert extract["content_list_sample"] == [{"type": "text", "page_idx": 0, "text": "Paper Title"}]
    assert Path(extract["markdown_path"]).is_file()
    assert "pdf_extract" in packet["codex_prompt"]


def test_pdf_ingest_auto_continues_when_mineru_is_missing(tmp_path: Path, monkeypatch, capsys) -> None:
    isolate_qmd_home(tmp_path, monkeypatch)
    vault = make_vault(tmp_path)
    (vault / ".env").write_text(
        "QMD_WIKI_COLLECTION=vault\nQMD_PAPERS_COLLECTION=vault\nA_INF_PDF_EXTRACTOR=auto\n",
        encoding="utf-8",
    )
    source = vault / "_raw" / "paper.pdf"
    source.write_bytes(b"%PDF fake test source")
    args = IngestArgs([str(source)])
    args.print_prompt = True

    monkeypatch.chdir(vault)
    monkeypatch.setattr(ingest.shutil, "which", lambda name: None if name == "mineru" else f"/usr/local/bin/{name}")
    monkeypatch.setattr(ingest.subprocess, "run", fake_qmd_run)

    assert cli.cmd_dispatch(args) == 0
    packet = json.loads(capsys.readouterr().out)
    extract = packet["sources"][0]["pdf_extract"]
    assert extract["status"] == "unavailable"
    assert "mineru executable not found" in extract["warnings"][0]


def test_pdf_ingest_explicit_mineru_fails_when_missing(tmp_path: Path, monkeypatch) -> None:
    isolate_qmd_home(tmp_path, monkeypatch)
    vault = make_vault(tmp_path)
    (vault / ".env").write_text("A_INF_PDF_EXTRACTOR=mineru\n", encoding="utf-8")
    source = vault / "_raw" / "paper.pdf"
    source.write_bytes(b"%PDF fake test source")
    args = IngestArgs([str(source)])
    args.print_prompt = True

    monkeypatch.chdir(vault)
    monkeypatch.setattr(ingest.shutil, "which", lambda name: None if name == "mineru" else f"/usr/local/bin/{name}")
    monkeypatch.setattr(ingest.subprocess, "run", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("subprocess.run called")))

    assert cli.cmd_dispatch(args) == 1
    assert not (vault / "_runs").exists()


def test_ingest_valid_plan_applies_pages_and_special_files(tmp_path: Path, monkeypatch) -> None:
    isolate_qmd_home(tmp_path, monkeypatch)
    vault = make_vault(tmp_path)
    source = vault / "note.md"
    source.write_text("hybrid ingest\n", encoding="utf-8")
    args = IngestArgs([str(source)])

    def fake_codex(command: list[str], prompt: str, cwd: Path, env: dict[str, str] | None = None) -> int:
        assert command[0] == "/usr/local/bin/codex"
        assert command[-1] != prompt
        plan_path = plan_path_from_prompt(prompt)
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        plan_path.write_text(json.dumps(page_plan(source)), encoding="utf-8")
        return 0

    monkeypatch.chdir(vault)
    monkeypatch.setattr(ingest.shutil, "which", lambda name: f"/usr/local/bin/{name}")
    monkeypatch.setattr(qmd_module.shutil, "which", lambda name: f"/usr/local/bin/{name}")
    monkeypatch.setattr(qmd_module.subprocess, "run", fake_qmd_run)
    monkeypatch.setattr(ingest, "call_codex_exec", fake_codex)

    assert cli.cmd_dispatch(args) == 0
    page = vault / "concepts" / "deterministic-ingest.md"
    assert page.is_file()
    manifest = json.loads((vault / ".manifest.json").read_text(encoding="utf-8"))
    assert str(source) in manifest["sources"]
    assert manifest["sources"][str(source)]["pages_created"] == ["concepts/deterministic-ingest.md"]
    archive = manifest["sources"][str(source)]
    assert archive["archive_dir"].startswith("_sources/")
    assert (vault / archive["original_path"]).read_text(encoding="utf-8") == "hybrid ingest\n"
    assert (vault / archive["extracted_path"]).read_text(encoding="utf-8") == "hybrid ingest\n"
    metadata = json.loads((vault / archive["archive_dir"] / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["pages"] == ["concepts/deterministic-ingest.md"]
    index = (vault / "index.md").read_text(encoding="utf-8")
    assert "[[concepts/deterministic-ingest|Deterministic Ingest]]" in index
    assert " - A deterministic shell around semantic wiki ingest." in index
    assert "tags: [a-inf]" in index
    log = (vault / "log.md").read_text(encoding="utf-8")
    assert "tags: [a-inf]" in log
    assert "INGEST" in log
    hot = (vault / "hot.md").read_text(encoding="utf-8")
    assert "tags: [a-inf]" in hot
    assert "Apply is deterministic." in hot


def test_url_ingest_valid_plan_applies_reference_and_manifest(tmp_path: Path, monkeypatch) -> None:
    isolate_qmd_home(tmp_path, monkeypatch)
    vault = make_vault(tmp_path)
    (vault / ".env").write_text("QMD_WIKI_COLLECTION=vault\nQMD_PAPERS_COLLECTION=vault\n", encoding="utf-8")
    url = "https://example.com/article"
    markdown = "# Example Article\n\nFetched body."
    args = IngestArgs([url])

    def fake_codex(command: list[str], prompt: str, cwd: Path, env: dict[str, str] | None = None) -> int:
        assert command[0] == "/usr/local/bin/codex"
        assert command[-1] != prompt
        plan_path = plan_path_from_prompt(prompt)
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        plan_path.write_text(json.dumps(url_plan(url, markdown)), encoding="utf-8")
        return 0

    monkeypatch.chdir(vault)
    monkeypatch.setattr(ingest.shutil, "which", lambda name: f"/usr/local/bin/{name}")
    monkeypatch.setattr(qmd_module.shutil, "which", lambda name: f"/usr/local/bin/{name}")
    monkeypatch.setattr(ingest, "fetch_url_html", fake_fetch_url_html(markdown))
    monkeypatch.setattr(ingest.subprocess, "run", fake_run_with_defuddle(markdown))
    monkeypatch.setattr(ingest, "call_codex_exec", fake_codex)

    assert cli.cmd_dispatch(args) == 0
    page = vault / "references" / "web-example-com-article.md"
    assert page.is_file()
    manifest = json.loads((vault / ".manifest.json").read_text(encoding="utf-8"))
    assert url in manifest["sources"]
    assert manifest["sources"][url]["source_type"] == "url"
    assert manifest["sources"][url]["source_url"] == url
    assert manifest["sources"][url]["pages_created"] == ["references/web-example-com-article.md"]
    assert (vault / manifest["sources"][url]["extracted_path"]).read_text(encoding="utf-8") == markdown + "\n"
    assert (vault / manifest["sources"][url]["original_path"]).read_text(encoding="utf-8") == markdown
    index = (vault / "index.md").read_text(encoding="utf-8")
    assert "[[references/web-example-com-article|Example Article]]" in index
    assert "INGEST" in (vault / "log.md").read_text(encoding="utf-8")
    assert "URL ingest uses deterministic extraction." in (vault / "hot.md").read_text(encoding="utf-8")


def test_reference_pages_normalize_backticked_source_archive_paths(tmp_path: Path, monkeypatch) -> None:
    isolate_qmd_home(tmp_path, monkeypatch)
    vault = make_vault(tmp_path)
    url = "https://example.com/article"
    markdown = "# Example Article\n\nFetched body."
    archive_id = f"{ingest.hash_bytes(markdown.encode('utf-8')).split(':', 1)[1][:16]}-example-com-article"
    archive_path = f"_sources/{archive_id}/extracted.md"
    args = IngestArgs([url])

    def fake_codex(command: list[str], prompt: str, cwd: Path, env: dict[str, str] | None = None) -> int:
        plan_path = plan_path_from_prompt(prompt)
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        plan = url_plan(url, markdown)
        plan["pages"][0]["body"] = (
            f"# Example Article\n\nFull extract: `{archive_path}`.\n\n"
            "```markdown\n"
            f"Literal example: `{archive_path}`\n"
            "```"
        )
        plan_path.write_text(json.dumps(plan), encoding="utf-8")
        return 0

    monkeypatch.chdir(vault)
    monkeypatch.setattr(ingest.shutil, "which", lambda name: f"/usr/local/bin/{name}")
    monkeypatch.setattr(qmd_module.shutil, "which", lambda name: f"/usr/local/bin/{name}")
    monkeypatch.setattr(ingest, "fetch_url_html", fake_fetch_url_html(markdown))
    monkeypatch.setattr(ingest.subprocess, "run", fake_run_with_defuddle(markdown))
    monkeypatch.setattr(ingest, "call_codex_exec", fake_codex)

    assert cli.cmd_dispatch(args) == 0
    page = (vault / "references" / "web-example-com-article.md").read_text(encoding="utf-8")
    assert f"Full extract: [[{archive_path}]]." in page
    assert f"Literal example: `{archive_path}`" in page


def test_url_ingest_archives_embedded_figures(tmp_path: Path, monkeypatch) -> None:
    isolate_qmd_home(tmp_path, monkeypatch)
    vault = make_vault(tmp_path)
    url = "https://example.com/slides.html"
    image = base64.b64encode(b"fake png").decode("ascii")
    html = f'<html><body><img alt="plot" src="data:image/png;base64,{image}"><img src="data:image/png;base64,{image}"></body></html>'
    markdown = "# Slides\n\n![plot](_sources/archive/figures/figure-001.png)"
    args = IngestArgs([url])

    def fake_codex(command: list[str], prompt: str, cwd: Path, env: dict[str, str] | None = None) -> int:
        plan_path = plan_path_from_prompt(prompt)
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        plan_path.write_text(
            json.dumps(
                url_plan(
                    url,
                    markdown,
                    path="references/web-example-com-slides-html.md",
                    content_hash=ingest.hash_bytes(html.encode("utf-8")),
                )
            ),
            encoding="utf-8",
        )
        return 0

    monkeypatch.chdir(vault)
    monkeypatch.setattr(ingest.shutil, "which", lambda name: f"/usr/local/bin/{name}")
    monkeypatch.setattr(qmd_module.shutil, "which", lambda name: f"/usr/local/bin/{name}")
    monkeypatch.setattr(ingest, "fetch_url_html", fake_fetch_url_html(html))
    monkeypatch.setattr(ingest.subprocess, "run", fake_run_with_defuddle(markdown))
    monkeypatch.setattr(ingest, "call_codex_exec", fake_codex)

    assert cli.cmd_dispatch(args) == 0
    manifest = json.loads((vault / ".manifest.json").read_text(encoding="utf-8"))
    entry = manifest["sources"][url]
    assert (vault / entry["original_path"]).read_text(encoding="utf-8") == html
    assert (vault / entry["extracted_path"]).read_text(encoding="utf-8") == markdown + "\n"
    assert (vault / entry["figures_dir"] / "figure-001.png").read_bytes() == b"fake png"
    assert len(entry["embedded_figures"]) == 1
    metadata = json.loads((vault / entry["archive_dir"] / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["embedded_figures"] == entry["embedded_figures"]


def test_pdf_ingest_archives_original_extract_and_figures(tmp_path: Path, monkeypatch) -> None:
    isolate_qmd_home(tmp_path, monkeypatch)
    vault = make_vault(tmp_path)
    (vault / ".env").write_text(
        "QMD_WIKI_COLLECTION=vault\nQMD_PAPERS_COLLECTION=vault\nA_INF_PDF_EXTRACTOR=mineru\n",
        encoding="utf-8",
    )
    source = vault / "_raw" / "paper.pdf"
    source.write_bytes(b"%PDF fake test source")
    full_markdown = "# Paper Title\n\nEquation: x^2.\n\nLate formula: y = Ax."
    packet_markdown = "# Paper Title\n\nEquation: x^2."
    args = IngestArgs([str(source)])

    def fake_codex(command: list[str], prompt: str, cwd: Path, env: dict[str, str] | None = None) -> int:
        plan_path = plan_path_from_prompt(prompt)
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        plan = page_plan(source, path="references/paper.md")
        plan["sources"][0]["source_type"] = "pdf"
        plan_path.write_text(json.dumps(plan), encoding="utf-8")
        return 0

    monkeypatch.chdir(vault)
    monkeypatch.setattr(ingest, "PDF_EXTRACT_MARKDOWN_MAX_CHARS", len(packet_markdown))
    monkeypatch.setattr(ingest.shutil, "which", lambda name: f"/usr/local/bin/{name}" if name in {"mineru", "qmd", "codex"} else None)
    monkeypatch.setattr(qmd_module.shutil, "which", lambda name: f"/usr/local/bin/{name}" if name in {"mineru", "qmd", "codex"} else None)
    monkeypatch.setattr(
        ingest.subprocess,
        "run",
        fake_run_with_mineru(full_markdown, [{"type": "image", "img_path": "images/fig1.png", "page_idx": 0}]),
    )
    monkeypatch.setattr(ingest, "call_codex_exec", fake_codex)

    assert cli.cmd_dispatch(args) == 0
    manifest = json.loads((vault / ".manifest.json").read_text(encoding="utf-8"))
    archive = manifest["sources"][str(source)]
    assert (vault / archive["original_path"]).read_bytes() == b"%PDF fake test source"
    assert (vault / archive["extracted_path"]).read_text(encoding="utf-8") == full_markdown + "\n"
    assert packet_markdown in (vault / archive["extracted_path"]).read_text(encoding="utf-8")
    assert (vault / archive["figures_dir"] / "images" / "fig1.png").read_bytes() == b"fake image"
    assert sorted(path.name for path in (vault / archive["archive_dir"]).rglob("*.pdf")) == ["original.pdf"]


def test_archive_can_be_disabled(tmp_path: Path, monkeypatch) -> None:
    isolate_qmd_home(tmp_path, monkeypatch)
    vault = make_vault(tmp_path)
    (vault / ".env").write_text("A_INF_ARCHIVE_SOURCES=false\n", encoding="utf-8")
    source = vault / "note.md"
    source.write_text("hybrid ingest\n", encoding="utf-8")
    args = IngestArgs([str(source)])

    def fake_codex(command: list[str], prompt: str, cwd: Path, env: dict[str, str] | None = None) -> int:
        plan_path = plan_path_from_prompt(prompt)
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        plan_path.write_text(json.dumps(page_plan(source)), encoding="utf-8")
        return 0

    monkeypatch.chdir(vault)
    monkeypatch.setattr(ingest.shutil, "which", lambda name: f"/usr/local/bin/{name}")
    monkeypatch.setattr(qmd_module.shutil, "which", lambda name: f"/usr/local/bin/{name}")
    monkeypatch.setattr(qmd_module.subprocess, "run", fake_qmd_run)
    monkeypatch.setattr(ingest, "call_codex_exec", fake_codex)

    assert cli.cmd_dispatch(args) == 0
    manifest = json.loads((vault / ".manifest.json").read_text(encoding="utf-8"))
    assert "archive_dir" not in manifest["sources"][str(source)]
    assert not (vault / "_sources").exists()


def test_ingest_missing_qmd_fails_before_codex(tmp_path: Path, monkeypatch, capsys) -> None:
    isolate_qmd_home(tmp_path, monkeypatch)
    vault = make_vault(tmp_path)
    source = vault / "note.md"
    source.write_text("hybrid ingest\n", encoding="utf-8")
    args = IngestArgs([str(source)])

    monkeypatch.chdir(vault)
    monkeypatch.setattr(qmd_module.shutil, "which", lambda name: None if name == "qmd" else "/usr/local/bin/codex")
    monkeypatch.setattr(ingest, "call_codex_exec", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("codex called")))

    assert cli.cmd_dispatch(args) == 127
    assert "npm install -g @tobilu/qmd" in capsys.readouterr().err


def test_ingest_checks_qmd_version_before_codex(tmp_path: Path, monkeypatch) -> None:
    isolate_qmd_home(tmp_path, monkeypatch)
    vault = make_vault(tmp_path)
    source = vault / "note.md"
    source.write_text("hybrid ingest\n", encoding="utf-8")
    args = IngestArgs([str(source)])
    calls: list[str] = []

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(" ".join(command[1:]))
        return subprocess.CompletedProcess(command, 0, stdout="qmd 2.1.0\n", stderr="")

    def fake_codex(command: list[str], prompt: str, cwd: Path, env: dict[str, str] | None = None) -> int:
        calls.append("codex")
        plan_path = plan_path_from_prompt(prompt)
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        plan_path.write_text(json.dumps(page_plan(source)), encoding="utf-8")
        return 0

    monkeypatch.chdir(vault)
    monkeypatch.setattr(ingest.shutil, "which", lambda name: f"/usr/local/bin/{name}")
    monkeypatch.setattr(qmd_module.shutil, "which", lambda name: f"/usr/local/bin/{name}")
    monkeypatch.setattr(qmd_module.subprocess, "run", fake_run)
    monkeypatch.setattr(ingest, "call_codex_exec", fake_codex)

    assert cli.cmd_dispatch(args) == 0
    assert calls == ["--version", "--version", "collection show vault", "--version", "update", "embed", "codex", "--version", "update", "embed"]


def test_ingest_invalid_plan_fails_without_wiki_writes(tmp_path: Path, monkeypatch) -> None:
    isolate_qmd_home(tmp_path, monkeypatch)
    vault = make_vault(tmp_path)
    source = vault / "note.md"
    source.write_text("hybrid ingest\n", encoding="utf-8")
    original_manifest = (vault / ".manifest.json").read_text(encoding="utf-8")
    args = IngestArgs([str(source)])

    def fake_codex(command: list[str], prompt: str, cwd: Path, env: dict[str, str] | None = None) -> int:
        plan_path = plan_path_from_prompt(prompt)
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        plan_path.write_text(json.dumps({"version": 1, "mode": "append", "sources": [], "pages": []}), encoding="utf-8")
        return 0

    monkeypatch.chdir(vault)
    monkeypatch.setattr(ingest.shutil, "which", lambda name: f"/usr/local/bin/{name}")
    monkeypatch.setattr(qmd_module.shutil, "which", lambda name: f"/usr/local/bin/{name}")
    monkeypatch.setattr(qmd_module.subprocess, "run", fake_qmd_run)
    monkeypatch.setattr(ingest, "call_codex_exec", fake_codex)

    assert cli.cmd_dispatch(args) == 1
    assert not (vault / "concepts" / "deterministic-ingest.md").exists()
    assert (vault / ".manifest.json").read_text(encoding="utf-8") == original_manifest


def test_append_mode_skips_unchanged_sources_in_packet(tmp_path: Path, monkeypatch, capsys) -> None:
    isolate_qmd_home(tmp_path, monkeypatch)
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
    monkeypatch.setattr(qmd_module.shutil, "which", lambda name: f"/usr/local/bin/{name}")
    monkeypatch.setattr(qmd_module.subprocess, "run", fake_qmd_run)

    assert cli.cmd_dispatch(args) == 0
    packet = json.loads(capsys.readouterr().out)
    assert packet["sources"] == []


def test_append_mode_skips_unchanged_url_sources_in_packet(tmp_path: Path, monkeypatch, capsys) -> None:
    isolate_qmd_home(tmp_path, monkeypatch)
    vault = make_vault(tmp_path)
    url = "https://example.com/article"
    markdown = "# Example Article\n\nFetched body."
    now = "2026-05-05T12:00:00+00:00"
    manifest = {
        "version": 1,
        "sources": {
            url: {
                "ingested_at": now,
                "modified_at": now,
                "content_hash": ingest.hash_bytes(markdown.encode("utf-8")),
                "size_bytes": len(markdown.encode("utf-8")),
                "source_type": "url",
                "source_url": url,
            }
        },
        "projects": {},
        "stats": {},
    }
    (vault / ".manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    args = IngestArgs([url])
    args.print_prompt = True

    monkeypatch.chdir(vault)
    monkeypatch.setattr(ingest.shutil, "which", lambda name: f"/usr/local/bin/{name}" if name in {"defuddle", "qmd"} else None)
    monkeypatch.setattr(qmd_module.shutil, "which", lambda name: f"/usr/local/bin/{name}" if name in {"defuddle", "qmd"} else None)
    monkeypatch.setattr(ingest, "fetch_url_html", fake_fetch_url_html(markdown))
    monkeypatch.setattr(ingest.subprocess, "run", fake_run_with_defuddle(markdown))

    assert cli.cmd_dispatch(args) == 0
    packet = json.loads(capsys.readouterr().out)
    assert packet["sources"] == []


def test_append_mode_includes_modified_url_sources_in_packet(tmp_path: Path, monkeypatch, capsys) -> None:
    isolate_qmd_home(tmp_path, monkeypatch)
    vault = make_vault(tmp_path)
    url = "https://example.com/article"
    markdown = "# Example Article\n\nFetched body."
    manifest = {
        "version": 1,
        "sources": {
            url: {
                "ingested_at": "2026-05-05T12:00:00+00:00",
                "modified_at": "2026-05-05T12:00:00+00:00",
                "content_hash": ingest.hash_bytes(b"old body"),
                "size_bytes": 8,
                "source_type": "url",
                "source_url": url,
            }
        },
        "projects": {},
        "stats": {},
    }
    (vault / ".manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    args = IngestArgs([url])
    args.print_prompt = True

    monkeypatch.chdir(vault)
    monkeypatch.setattr(ingest.shutil, "which", lambda name: f"/usr/local/bin/{name}" if name in {"defuddle", "qmd"} else None)
    monkeypatch.setattr(qmd_module.shutil, "which", lambda name: f"/usr/local/bin/{name}" if name in {"defuddle", "qmd"} else None)
    monkeypatch.setattr(ingest, "fetch_url_html", fake_fetch_url_html(markdown))
    monkeypatch.setattr(ingest.subprocess, "run", fake_run_with_defuddle(markdown))

    assert cli.cmd_dispatch(args) == 0
    packet = json.loads(capsys.readouterr().out)
    assert packet["sources"][0]["manifest_key"] == url
    assert packet["sources"][0]["status"] == "modified"


def test_url_ingest_and_data_flag_use_hybrid_path(tmp_path: Path, monkeypatch) -> None:
    vault = make_vault(tmp_path)
    calls: list[str] = []

    def fake_run_dispatch(dispatch: cli.Dispatch, args: object) -> int:
        calls.append(dispatch.skill)
        return 0

    def fake_run_hybrid(_args: object, _vault: Path) -> int:
        calls.append("wiki-ingest")
        return 0

    monkeypatch.chdir(vault)
    monkeypatch.setattr(cli, "run_dispatch", fake_run_dispatch)
    monkeypatch.setattr(ingest, "run_hybrid_ingest", fake_run_hybrid)

    assert cli.cmd_dispatch(IngestArgs(["https://example.com/article"])) == 0
    data_args = IngestArgs(["export.json"])
    data_args.data = True
    assert cli.cmd_dispatch(data_args) == 0
    assert calls == ["wiki-ingest", "wiki-ingest"]


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
    root = vault / "_runs"
    for index in range(25):
        (root / f"20260505T0000{index:02d}Z").mkdir(parents=True)

    ingest.prune_runs(vault)

    assert len([path for path in root.iterdir() if path.is_dir()]) == 20
