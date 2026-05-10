from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

from a_inf import cli
from a_inf import query
from a_inf.qmd import QmdInfo


def write_page(
    vault: Path,
    rel: str,
    *,
    title: str,
    tags: list[str],
    summary: str,
    aliases: list[str] | None = None,
    updated: str = "2026-05-05",
    lifecycle: str = "draft",
    body: str = "",
) -> Path:
    path = vault / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                "---",
                f'title: "{title}"',
                f'category: "{rel.split("/", 1)[0]}"',
                f"tags: {json.dumps(tags)}",
                f"aliases: {json.dumps(aliases or [])}",
                f'summary: "{summary}"',
                "sources: [test]",
                "created: 2026-05-05",
                f"updated: {updated}",
                f"lifecycle: {lifecycle}",
                "lifecycle_changed: 2026-05-05",
                "---",
                "",
                body,
            ]
        ),
        encoding="utf-8",
    )
    return path


def fake_qmd(vault: Path) -> QmdInfo:
    return QmdInfo(
        binary="/usr/local/bin/qmd",
        version="qmd 2.1.0",
        wiki_collection="vault",
        papers_collection="vault",
        index_path=str(vault / ".a-inf" / "qmd" / "index.sqlite"),
        vault_path=str(vault),
    )


def test_classify_query_modes() -> None:
    modes = query.classify_query("quick answer public only how does A relate to B")

    assert modes.index_only is True
    assert modes.filtered is True
    assert modes.query_type == "relationship"


def test_page_registry_reads_frontmatter_and_index_entries(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    write_page(
        vault,
        "concepts/rate-limiting.md",
        title="Rate Limiting",
        tags=["systems", "visibility/internal"],
        aliases=["throttling"],
        summary="Limits request throughput.",
        lifecycle="verified",
    )
    index = "- [[concepts/rate-limiting|Rate Limiting]] - Limits request throughput. (#systems)"

    registry = query.build_page_registry(vault, index)
    page = registry["concepts/rate-limiting.md"]

    assert page.title == "Rate Limiting"
    assert page.aliases == ["throttling"]
    assert "visibility/internal" in page.tags
    assert page.lifecycle == "verified"
    assert page.index_entry == index


def test_page_registry_reads_block_list_tags_for_filtering(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    path = vault / "concepts" / "block-list.md"
    path.parent.mkdir(parents=True)
    path.write_text(
        """---
title: Block List
category: concepts
tags:
  - systems
  - visibility/pii
aliases:
  - Block Alias
summary: Block list metadata.
sources: [test]
created: 2026-05-05
updated: 2026-05-05
---

Body.
""",
        encoding="utf-8",
    )

    page = query.build_page_registry(vault)["concepts/block-list.md"]

    assert page.tags == ["systems", "visibility/pii"]
    assert page.aliases == ["Block Alias"]


def test_qmd_command_uses_structured_no_rerank(tmp_path: Path, monkeypatch) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    write_page(vault, "concepts/query.md", title="Query", tags=["wiki"], summary="Query path.")
    calls: list[list[str]] = []

    def fake_run_qmd(_qmd: QmdInfo, args: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(
            ["/usr/local/bin/qmd", *args],
            0,
            stdout=json.dumps(
                [{"file": "qmd://vault/concepts/query.md", "score": 0.72, "snippet": "Query snippet."}]
            ),
            stderr="",
        )

    monkeypatch.setattr(query, "run_qmd", fake_run_qmd)

    results, warnings = query.run_qmd_query(
        vault,
        {"QMD_WIKI_COLLECTION": "vault"},
        fake_qmd(vault),
        "deterministic query retrieval packet",
        query.build_page_registry(vault),
    )

    assert warnings == []
    assert results[0].path == "concepts/query.md"
    assert calls[0][:6] == ["query", "--json", "--no-rerank", "-n", "10", "-c"]
    assert calls[0][6] == "vault"
    assert calls[0][7].startswith("lex: deterministic query retrieval packet\nvec:")


def test_candidate_ranking_is_stable_and_filtered(tmp_path: Path, monkeypatch) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    write_page(vault, "concepts/public.md", title="Packet Query", tags=["wiki"], summary="A query packet.")
    write_page(
        vault,
        "concepts/private.md",
        title="Private Packet",
        tags=["wiki", "visibility/internal"],
        summary="Internal query packet.",
    )
    monkeypatch.setattr(
        query,
        "run_qmd",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            ["/usr/local/bin/qmd"],
            0,
            stdout=json.dumps(
                [
                    {"file": "qmd://vault/concepts/private.md", "score": 0.99, "snippet": "private"},
                    {"file": "qmd://vault/concepts/public.md", "score": 0.5, "snippet": "public"},
                ]
            ),
            stderr="",
        ),
    )

    packet = query.build_retrieval_packet(
        vault,
        {"QMD_WIKI_COLLECTION": "vault"},
        "public only packet query",
        fake_qmd(vault),
    )

    assert [candidate["path"] for candidate in packet["candidates"]] == ["concepts/public.md"]
    assert packet["filtered"] is True


def test_cli_query_routes_to_deterministic_engine(tmp_path: Path, monkeypatch) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / ".manifest.json").write_text('{"version": 1}\n', encoding="utf-8")
    calls: list[tuple[Path, dict[str, str], list[str]]] = []

    def fake_run_query(args: object, query_vault: Path, config: dict[str, str]) -> int:
        calls.append((query_vault, config, getattr(args, "args")))
        return 0

    monkeypatch.chdir(vault)
    monkeypatch.setattr(cli, "load_wiki_config", lambda _vault: {"QMD_WIKI_COLLECTION": "vault"})
    monkeypatch.setattr(query, "run_query", fake_run_query)

    result = cli.cmd_dispatch(
        SimpleNamespace(alias="query", args=["what", "now"], print_prompt=False, no_codex=False)
    )

    assert result == 0
    assert calls == [(vault, {"QMD_WIKI_COLLECTION": "vault"}, ["what", "now"])]


def test_print_prompt_contains_packet_without_codex(tmp_path: Path, monkeypatch, capsys) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    write_page(
        vault,
        "concepts/query.md",
        title="Query",
        tags=["wiki"],
        summary="Deterministic query packets.",
        updated="2025-01-01",
        lifecycle="verified",
        body="This links to [[entities/qmd]].",
    )
    write_page(vault, "entities/qmd.md", title="QMD", tags=["tool"], summary="Markdown search.")
    monkeypatch.setattr(query, "ensure_qmd_collection", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(query, "resolve_qmd", lambda _config, _vault: fake_qmd(vault))
    monkeypatch.setattr(
        query,
        "run_qmd",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            ["/usr/local/bin/qmd"],
            0,
            stdout=json.dumps(
                [{"file": "qmd://vault/concepts/query.md", "score": 0.8, "snippet": "Query packet snippet."}]
            ),
            stderr="",
        ),
    )
    monkeypatch.setattr(query.shutil, "which", lambda _name: (_ for _ in ()).throw(AssertionError("codex called")))

    result = query.run_query(
        SimpleNamespace(
            args=["what", "do", "I", "know", "about", "query"],
            print_prompt=True,
            no_codex=False,
            codex_bin="codex",
            sandbox="workspace-write",
            add_dir=[],
        ),
        vault,
        {"QMD_WIKI_COLLECTION": "vault"},
    )

    output = capsys.readouterr().out
    assert result == 0
    assert "Retrieval packet:" in output
    assert '"path": "concepts/query.md"' in output
    assert '"page": "concepts/query.md"' in output
    assert "VERIFIED but stale" in output


def test_exact_math_query_includes_archived_source_details(tmp_path: Path, monkeypatch) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    page = write_page(
        vault,
        "references/paper.md",
        title="Paper",
        tags=["math"],
        summary="Paper summary.",
        body="Key source card.",
    )
    archive_dir = vault / ".a-inf" / "sources" / "abc-paper"
    archive_dir.mkdir(parents=True)
    extracted = archive_dir / "extracted.md"
    extracted.write_text("The objective is min_x ||Ax-b||^2 with Gauss-Newton updates.", encoding="utf-8")
    (vault / ".manifest.json").write_text(
        json.dumps(
            {
                "version": 1,
                "sources": {
                    "paper.pdf": {
                        "source_type": "pdf",
                        "archive_id": "abc-paper",
                        "archive_dir": ".a-inf/sources/abc-paper",
                        "extracted_path": ".a-inf/sources/abc-paper/extracted.md",
                        "pages_created": ["references/paper.md"],
                        "pages_updated": [],
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        query,
        "run_qmd",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            ["/usr/local/bin/qmd"],
            0,
            stdout=json.dumps(
                [{"file": "qmd://vault/references/paper.md", "score": 0.8, "snippet": "Paper snippet."}]
            ),
            stderr="",
        ),
    )

    packet = query.build_retrieval_packet(
        vault,
        {"QMD_WIKI_COLLECTION": "vault"},
        "exact equation for Gauss-Newton objective",
        fake_qmd(vault),
    )

    assert page.is_file()
    assert packet["source_details"][0]["extracted_path"] == ".a-inf/sources/abc-paper/extracted.md"
    assert "min_x" in packet["source_details"][0]["snippets"][0]


def test_normal_query_omits_source_details(tmp_path: Path, monkeypatch) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    write_page(vault, "references/paper.md", title="Paper", tags=["math"], summary="Paper summary.")
    archive_dir = vault / ".a-inf" / "sources" / "abc-paper"
    archive_dir.mkdir(parents=True)
    (archive_dir / "extracted.md").write_text("Detailed source text.", encoding="utf-8")
    (vault / ".manifest.json").write_text(
        json.dumps(
            {
                "sources": {
                    "paper.pdf": {
                        "extracted_path": ".a-inf/sources/abc-paper/extracted.md",
                        "pages_created": ["references/paper.md"],
                        "pages_updated": [],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        query,
        "run_qmd",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            ["/usr/local/bin/qmd"],
            0,
            stdout=json.dumps(
                [{"file": "qmd://vault/references/paper.md", "score": 0.8, "snippet": "Paper snippet."}]
            ),
            stderr="",
        ),
    )

    packet = query.build_retrieval_packet(vault, {"QMD_WIKI_COLLECTION": "vault"}, "paper summary", fake_qmd(vault))

    assert packet["source_details"] == []


def test_weak_query_retrieval_auto_includes_source_details(tmp_path: Path, monkeypatch) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    write_page(vault, "references/paper.md", title="Paper", tags=["math"], summary="Sparse.")
    archive_dir = vault / ".a-inf" / "sources" / "abc-paper"
    archive_dir.mkdir(parents=True)
    (archive_dir / "extracted.md").write_text("Latent smoother objective details.", encoding="utf-8")
    (vault / ".manifest.json").write_text(
        json.dumps(
            {
                "sources": {
                    "paper.pdf": {
                        "extracted_path": ".a-inf/sources/abc-paper/extracted.md",
                        "pages_created": ["references/paper.md"],
                        "pages_updated": [],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        query,
        "run_qmd",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            ["/usr/local/bin/qmd"],
            0,
            stdout=json.dumps(
                [{"file": "qmd://vault/references/paper.md", "score": 0.05, "snippet": "Sparse."}]
            ),
            stderr="",
        ),
    )

    packet = query.build_retrieval_packet(vault, {"QMD_WIKI_COLLECTION": "vault"}, "latent smoother", fake_qmd(vault))

    assert packet["source_details"][0]["manifest_key"] == "paper.pdf"


def test_filtered_query_does_not_expose_internal_source_details(tmp_path: Path, monkeypatch) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    write_page(
        vault,
        "references/private.md",
        title="Private Paper",
        tags=["math", "visibility/internal"],
        summary="Internal paper.",
    )
    archive_dir = vault / ".a-inf" / "sources" / "private"
    archive_dir.mkdir(parents=True)
    (archive_dir / "extracted.md").write_text("Secret equation.", encoding="utf-8")
    (vault / ".manifest.json").write_text(
        json.dumps(
            {
                "sources": {
                    "private.pdf": {
                        "extracted_path": ".a-inf/sources/private/extracted.md",
                        "pages_created": ["references/private.md"],
                        "pages_updated": [],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        query,
        "run_qmd",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            ["/usr/local/bin/qmd"],
            0,
            stdout=json.dumps(
                [{"file": "qmd://vault/references/private.md", "score": 0.9, "snippet": "private"}]
            ),
            stderr="",
        ),
    )

    packet = query.build_retrieval_packet(
        vault,
        {"QMD_WIKI_COLLECTION": "vault"},
        "public only exact equation",
        fake_qmd(vault),
    )

    assert packet["candidates"] == []
    assert packet["source_details"] == []
