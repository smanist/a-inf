from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

from a_inf import cli
from a_inf import ideate
from a_inf import ingest
from a_inf import lint
from a_inf import query


def write_page(vault: Path, rel: str, *, title: str, tags: list[str] | None = None, body: str = "") -> Path:
    path = vault / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                "---",
                f"title: {json.dumps(title)}",
                f"category: {json.dumps(rel.split('/', 1)[0])}",
                f"tags: {json.dumps(tags or ['math'])}",
                "sources: []",
                f"summary: {json.dumps(title + ' summary')}",
                "created: 2026-05-07T00:00:00+00:00",
                "updated: 2026-05-07T00:00:00+00:00",
                "---",
                "",
                body or f"# {title}\n\nBody for {title}.",
            ]
        ),
        encoding="utf-8",
    )
    return path


def make_vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / ".manifest.json").write_text('{"version": 1, "sources": {}, "projects": {}, "stats": {}}\n', encoding="utf-8")
    (vault / "index.md").write_text("# Wiki Index\n", encoding="utf-8")
    (vault / "hot.md").write_text("# Hot Cache\n", encoding="utf-8")
    (vault / "AGENTS.md").write_text("# Repository Instructions\n", encoding="utf-8")
    return vault


def args(**overrides: object) -> SimpleNamespace:
    values = {
        "alias": "ideate",
        "args": ["use", "laplacian", "smoothing"],
        "entry": [],
        "print_prompt": False,
        "no_codex": False,
        "json": False,
        "codex_bin": "codex",
        "sandbox": "workspace-write",
        "add_dir": [],
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_ideate_print_prompt_includes_skill_packet_and_output(tmp_path: Path, monkeypatch, capsys) -> None:
    vault = make_vault(tmp_path)
    monkeypatch.chdir(vault)
    monkeypatch.setattr(ideate, "resolve_qmd", lambda *_args, **_kwargs: None)

    result = cli.cmd_dispatch(args(print_prompt=True))

    assert result == 0
    output = capsys.readouterr().out
    assert "wiki-ideate" in output
    assert "packet.json" in output
    assert "ideas/use-laplacian-smoothing.md" in output


def test_entry_resolution_accepts_path_and_title(tmp_path: Path, monkeypatch) -> None:
    vault = make_vault(tmp_path)
    path_page = write_page(vault, "concepts/laplace.md", title="Laplace Operator")
    title_page = write_page(vault, "references/solver.md", title="Sparse Solver")
    monkeypatch.setattr(ideate, "resolve_qmd", lambda *_args, **_kwargs: None)

    packet = ideate.build_ideation_packet(
        vault,
        {},
        "better smoothing",
        [path_page.relative_to(vault).as_posix(), title_page.stem.replace("-", " "), "Sparse Solver"],
        vault / "ideas" / "better-smoothing.md",
    )

    explicit = {item["path"] for item in packet["explicit_context"]}
    assert "concepts/laplace.md" in explicit
    assert "references/solver.md" in explicit
    assert len(packet["explicit_context"]) == 2


def test_qmd_failure_continues_with_thin_context(tmp_path: Path, monkeypatch, capsys) -> None:
    vault = make_vault(tmp_path)
    monkeypatch.setattr(ideate, "resolve_qmd", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(
        ideate,
        "run_qmd",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(["qmd"], 1, "", "boom"),
    )

    result = ideate.run_ideate(args(no_codex=True), vault, {})

    assert result == 0
    output = capsys.readouterr().out
    assert "qmd query failed" in output
    assert "ideation skipped by --no-codex" in output


def test_unique_output_path_uses_suffix(tmp_path: Path) -> None:
    vault = make_vault(tmp_path)
    (vault / "ideas").mkdir()
    (vault / "ideas" / "foo.md").write_text("existing\n", encoding="utf-8")

    assert ideate.unique_output_path(vault, "foo") == vault / "ideas" / "foo-2.md"


def test_missing_codex_output_fails_validation(tmp_path: Path, monkeypatch, capsys) -> None:
    vault = make_vault(tmp_path)
    monkeypatch.setattr(ideate, "resolve_qmd", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(ideate.shutil, "which", lambda _binary: "/usr/local/bin/codex")
    monkeypatch.setattr(ideate.subprocess, "call", lambda *_args, **_kwargs: 0)

    result = ideate.run_ideate(args(args=["foo"]), vault, {})

    assert result == 1
    assert "did not write idea packet" in capsys.readouterr().out


def test_output_without_a_inf_tag_fails_validation(tmp_path: Path, monkeypatch, capsys) -> None:
    vault = make_vault(tmp_path)
    monkeypatch.setattr(ideate, "resolve_qmd", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(ideate.shutil, "which", lambda _binary: "/usr/local/bin/codex")

    def fake_call(command: list[str], cwd: Path, env: dict[str, str] | None = None) -> int:
        marker = "Write Markdown idea packet to: "
        output_line = next(line for line in command[-1].splitlines() if line.startswith(marker))
        output_path = Path(output_line[len(marker) :])
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("---\ntitle: Idea\n---\n\n# Idea\n", encoding="utf-8")
        return 0

    monkeypatch.setattr(ideate.subprocess, "call", fake_call)

    result = ideate.run_ideate(args(args=["foo"]), vault, {})

    assert result == 1
    assert "tags: [a-inf]" in capsys.readouterr().out


def test_ideas_not_in_canonical_wiki_dirs() -> None:
    assert "ideas" not in ingest.WIKI_PAGE_DIRS
    assert "ideas" not in query.WIKI_PAGE_DIRS
    assert "ideas" not in lint.CONTENT_DIRS
    assert "ideas" not in cli.WIKI_PAGE_DIRS
