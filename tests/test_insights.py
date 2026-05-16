from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

from a_inf import cli
from a_inf import insights


def write_page(vault: Path, rel: str, *, tags: list[str] | None = None, body: str = "") -> Path:
    path = vault / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    title = path.stem.replace("-", " ").title()
    text = [
        "---",
        f"title: {json.dumps(title)}",
        f"category: {json.dumps(rel.split('/', 1)[0])}",
        f"tags: {json.dumps(tags or ['ml'])}",
        "sources: [test]",
        f"summary: {json.dumps(title + ' summary')}",
        "created: 2026-05-05T00:00:00+00:00",
        "updated: 2026-05-05T00:00:00+00:00",
        "lifecycle: draft",
        "base_confidence: 0.5",
        "---",
        "",
        body,
    ]
    path.write_text("\n".join(text), encoding="utf-8")
    return path


def make_vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / ".manifest.json").write_text('{"version": 1, "sources": {}, "projects": {}, "stats": {}}\n', encoding="utf-8")
    (vault / "index.md").write_text("# Wiki Index\n", encoding="utf-8")
    (vault / "log.md").write_text("# Wiki Log\n", encoding="utf-8")
    (vault / "hot.md").write_text("# Hot Cache\n", encoding="utf-8")
    return vault


def add_insight_graph(vault: Path) -> None:
    write_page(
        vault,
        "concepts/hub.md",
        tags=["ml", "systems"],
        body="Connects [[entities/sink]], [[concepts/peer]], [[references/ref-0]], and [[entities/tool-0]].",
    )
    write_page(vault, "concepts/peer.md", tags=["ml"], body="Returns to [[concepts/hub]].")
    write_page(
        vault,
        "concepts/sparse.md",
        tags=["ml"],
        body="This possible relationship is unclear. ^[ambiguous] ^[inferred] See [[entities/sink]].",
    )
    write_page(vault, "entities/sink.md", tags=["entity"], body="")
    for index in range(10):
        tag = "ml" if index < 5 else "ops"
        write_page(
            vault,
            f"references/ref-{index}.md",
            tags=[tag],
            body="Mentions [[concepts/hub]] and [[entities/sink]].",
        )
    for index in range(6):
        write_page(vault, f"entities/tool-{index}.md", tags=["systems"], body="")


def insight_args(**overrides: object) -> SimpleNamespace:
    values = {
        "alias": "insights",
        "args": [],
        "json": False,
        "no_codex": True,
        "print_prompt": False,
        "no_log": False,
        "vscode": False,
        "vscode_bin": "code",
        "codex_bin": "codex",
        "sandbox": "workspace-write",
        "add_dir": [],
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def only_insights_output(vault: Path) -> Path:
    outputs = sorted((vault / "_runs").glob("insights-*/_insights.md"))
    assert len(outputs) == 1
    return outputs[0]


def test_cli_parser_accepts_insights_vscode_flags() -> None:
    parser = cli.build_parser()

    args = parser.parse_args(["insights", "--vscode", "--vscode-bin", "code-insiders", "--json"])

    assert args.alias == "insights"
    assert args.vscode is True
    assert args.vscode_bin == "code-insiders"
    assert args.json is True


def test_build_insights_packet_computes_deterministic_graph_sections(tmp_path: Path) -> None:
    vault = make_vault(tmp_path)
    add_insight_graph(vault)
    (vault / "_insights.md").write_text(
        '<!-- GRAPH_SNAPSHOT: {"nodes":["concepts/old.md","concepts/hub.md"],'
        '"edges":[["concepts/old.md","concepts/hub.md"]]} -->\n',
        encoding="utf-8",
    )

    packet = insights.build_insights_packet(vault, {}, vault / "_runs/insights/explanations.json")

    assert packet["summary"]["pages_scanned"] == 20
    assert packet["anchors"][0]["page"] == "entities/sink.md"
    assert packet["anchors"][0]["note"] == "sink hub - wiki-fixlink candidate"
    assert any(item["tag"] == "systems" and item["cohesion"] < 0.15 for item in packet["tag_cohesion"])
    assert any(item["source"] == "concepts/sparse.md" and item["target"] == "entities/sink.md" for item in packet["surprising_connections"])
    assert any(item["page"] == "entities/sink.md" for item in packet["orphan_adjacent"])
    assert packet["delta"]["removed_pages"] == ["concepts/old.md"]
    assert "concepts/sparse.md" in packet["delta"]["new_pages"]
    assert packet["questions"]


def test_build_insights_packet_prefers_previous_run_snapshot(tmp_path: Path) -> None:
    vault = make_vault(tmp_path)
    add_insight_graph(vault)
    (vault / "_insights.md").write_text(
        '<!-- GRAPH_SNAPSHOT: {"nodes":["concepts/root-only.md"],"edges":[]} -->\n',
        encoding="utf-8",
    )
    previous_output = vault / "_runs" / "insights-20260515T000000Z" / "_insights.md"
    previous_output.parent.mkdir(parents=True)
    previous_output.write_text(
        '<!-- GRAPH_SNAPSHOT: {"nodes":["concepts/previous-run.md"],"edges":[]} -->\n',
        encoding="utf-8",
    )

    packet = insights.build_insights_packet(vault, {}, vault / "_runs/insights/explanations.json")

    assert packet["delta"]["removed_pages"] == ["concepts/previous-run.md"]
    assert "concepts/root-only.md" not in packet["delta"]["removed_pages"]


def test_run_insights_no_codex_writes_markdown_log_and_syncs_qmd(tmp_path: Path, monkeypatch, capsys) -> None:
    vault = make_vault(tmp_path)
    add_insight_graph(vault)
    ensure_calls: list[Path] = []
    sync_calls: list[Path] = []
    monkeypatch.setattr(insights, "ensure_qmd_collection", lambda vault_arg, _config: ensure_calls.append(vault_arg) or True)
    monkeypatch.setattr(insights, "sync_qmd", lambda vault_arg, _config: sync_calls.append(vault_arg) or True)

    result = insights.run_insights(insight_args(no_codex=True), vault, {})

    assert result == 0
    output_path = only_insights_output(vault)
    output = output_path.read_text(encoding="utf-8")
    assert "Wiki Insights" in output
    assert "tags: [a-inf]" in output
    assert "GRAPH_SNAPSHOT" in output
    assert not (vault / "_insights.md").exists()
    assert "WIKI_INSIGHTS" in (vault / "log.md").read_text(encoding="utf-8")
    assert ensure_calls == [vault]
    assert sync_calls == [vault]
    report = capsys.readouterr().out
    assert "**Status:** completed" in report
    assert f"**Output:** {output_path.relative_to(vault).as_posix()}" in report


def test_run_insights_vscode_opens_generated_markdown(tmp_path: Path, monkeypatch) -> None:
    vault = make_vault(tmp_path)
    add_insight_graph(vault)
    opened: list[list[str]] = []
    monkeypatch.setattr(insights, "ensure_qmd_collection", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(insights, "sync_qmd", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(insights.shutil, "which", lambda name: f"/usr/local/bin/{name}")
    monkeypatch.setattr(
        insights.subprocess,
        "run",
        lambda command, **_kwargs: opened.append(command) or subprocess.CompletedProcess(command, 0),
    )

    result = insights.run_insights(insight_args(no_codex=True, vscode=True), vault, {})

    output_path = only_insights_output(vault)
    assert result == 0
    assert opened == [["/usr/local/bin/code", str(output_path)]]


def test_default_insights_invokes_codex_and_merges_valid_explanations(tmp_path: Path, monkeypatch) -> None:
    vault = make_vault(tmp_path)
    add_insight_graph(vault)

    def fake_call(command: list[str], cwd: Path, env: dict[str, str] | None = None) -> int:
        prompt = command[-1]
        packet_path = Path(prompt.split("Deterministic packet path: ", 1)[1].splitlines()[0])
        explanations_path = Path(prompt.split("Write explanation JSON to: ", 1)[1].splitlines()[0])
        packet = json.loads(packet_path.read_text(encoding="utf-8"))
        explanations_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "status": "completed",
                    "explanations": [
                        {
                            "id": packet["anchors"][0]["id"],
                            "explanation": "This page is a sink because many paths arrive here without outbound context.",
                        }
                    ],
                    "warnings": [],
                }
            ),
            encoding="utf-8",
        )
        return 0

    monkeypatch.setattr(insights.shutil, "which", lambda _: "/usr/local/bin/codex")
    monkeypatch.setattr(subprocess, "call", fake_call)

    result = insights.run_insights(insight_args(no_codex=False, sandbox="read-only", no_log=True), vault, {})

    assert result == 0
    assert "many paths arrive here" in only_insights_output(vault).read_text(encoding="utf-8")
    assert "WIKI_INSIGHTS" not in (vault / "log.md").read_text(encoding="utf-8")


def test_invalid_explanation_json_keeps_deterministic_report(tmp_path: Path, monkeypatch, capsys) -> None:
    vault = make_vault(tmp_path)
    add_insight_graph(vault)
    monkeypatch.setattr(insights.shutil, "which", lambda _: "/usr/local/bin/codex")
    monkeypatch.setattr(subprocess, "call", lambda *_args, **_kwargs: 0)

    result = insights.run_insights(insight_args(no_codex=False, sandbox="read-only", no_log=True), vault, {})

    assert result == 0
    output = only_insights_output(vault).read_text(encoding="utf-8")
    assert "sink hub - wiki-fixlink candidate" in output
    assert "could not read explanation JSON" in capsys.readouterr().out


def test_skip_small_vault_does_not_write_or_log(tmp_path: Path, capsys) -> None:
    vault = make_vault(tmp_path)
    write_page(vault, "concepts/one.md")
    original_log = (vault / "log.md").read_text(encoding="utf-8")

    result = insights.run_insights(insight_args(no_codex=True), vault, {})

    assert result == 0
    assert not (vault / "_insights.md").exists()
    assert not list((vault / "_runs").glob("insights-*/_insights.md"))
    assert (vault / "log.md").read_text(encoding="utf-8") == original_log
    assert "**Status:** skipped" in capsys.readouterr().out


def test_read_only_insights_does_not_sync_qmd(tmp_path: Path, monkeypatch) -> None:
    vault = make_vault(tmp_path)
    add_insight_graph(vault)
    monkeypatch.setattr(insights, "ensure_qmd_collection", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("qmd called")))
    monkeypatch.setattr(insights, "sync_qmd", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("qmd called")))

    result = insights.run_insights(insight_args(no_codex=True, sandbox="read-only"), vault, {})

    assert result == 0
    assert only_insights_output(vault).exists()
