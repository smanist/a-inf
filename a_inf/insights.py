from __future__ import annotations

from collections import Counter, defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from a_inf import lint
from a_inf.ingest import load_wiki_config, read_optional, write_json
from a_inf.managed_files import ensure_managed_tag, managed_tags
from a_inf.qmd import ensure_qmd_collection, qmd_env, sync_qmd
from a_inf.runs import timestamped_run_dir


MIN_INSIGHT_PAGES = 20
MAX_EXPLANATION_CHARS = 280


@dataclass(frozen=True)
class Run:
    run_dir: Path
    packet_path: Path
    explanations_path: Path
    output_path: Path
    report_path: Path


def run_insights(args: Any, vault: Path, config: dict[str, str] | None = None) -> int:
    config = config or load_wiki_config(vault)
    run = create_run(vault)
    packet = build_insights_packet(vault, config, run.explanations_path)
    write_json(run.packet_path, packet)

    if packet["summary"]["pages_scanned"] < MIN_INSIGHT_PAGES:
        warning = f"vault has fewer than {MIN_INSIGHT_PAGES} content pages; graph insights skipped"
        report = build_report(packet, status="skipped", warnings=[warning])
        write_json(run.report_path, report)
        print_report(report, json_output=getattr(args, "json", False))
        return 0

    if getattr(args, "print_prompt", False):
        print(build_prompt(vault, run.packet_path, run.explanations_path))
        return 0

    warnings: list[str] = []
    if getattr(args, "no_codex", False):
        explanation_result = {"status": "not_run", "explanations": {}, "warnings": ["explanation enrichment skipped by --no-codex"]}
    else:
        explanation_result = run_explanation_enrichment(args, vault, run)
    warnings.extend(str(item) for item in explanation_result.get("warnings", []) if str(item))

    output_text = render_insights_markdown(packet, explanation_result)
    run.output_path.write_text(output_text, encoding="utf-8")
    if not getattr(args, "no_log", False):
        append_log(vault, packet)

    qmd_warning: list[str] = []
    if getattr(args, "sandbox", "workspace-write") != "read-only":
        if ensure_qmd_collection(vault, config):
            if not sync_qmd(vault, config):
                qmd_warning.append("QMD sync failed after insights; wiki files were still updated.")
        else:
            qmd_warning.append("QMD sync skipped or failed after insights; wiki files were still updated.")

    report = build_report(
        packet,
        status="completed",
        explanation_status=str(explanation_result.get("status") or "unknown"),
        warnings=[*warnings, *qmd_warning],
        output_path=run.output_path.relative_to(vault).as_posix(),
    )
    write_json(run.report_path, report)
    print_report(report, json_output=getattr(args, "json", False))
    return 0


def build_insights_packet(vault: Path, config: dict[str, str], explanations_path: Path | None = None) -> dict[str, Any]:
    pages = lint.build_page_registry(vault)
    links = lint.build_link_graph(vault, pages)
    snapshot = build_snapshot(pages, links)
    previous_snapshot = read_previous_snapshot(previous_insights_output(vault))
    anchors = anchor_pages(pages, links)
    cohesion = tag_cohesion(pages, links)
    bridges = bridge_pages(pages, links)
    surprising = surprising_connections(pages, links)
    orphan_adjacent = orphan_adjacent_pages(anchors, links)
    clusters = rough_clusters(anchors, pages)
    delta = graph_delta(previous_snapshot, snapshot)
    questions = suggested_questions(pages, links, bridges, cohesion, orphan_adjacent)
    item_ids = insight_item_ids(anchors, bridges, cohesion, surprising, orphan_adjacent, clusters, questions)

    return {
        "version": 1,
        "generated_at": now_iso(),
        "vault": str(vault),
        "link_format": config.get("OBSIDIAN_LINK_FORMAT") or config.get("link_format") or "wikilink",
        "explanations_path": str(explanations_path) if explanations_path else "",
        "summary": {
            "pages_scanned": len(pages),
            "resolved_edges": len(links["resolved_edges"]),
            "broken_edges": len(links["broken_edges"]),
            "anchors": len(anchors),
            "bridges": len(bridges),
            "cohesion_checked": len(cohesion),
            "surprising": len(surprising),
            "questions": len(questions),
        },
        "page_registry": {
            rel: {
                "title": page.title,
                "category": page.category,
                "tags": page.tags,
                "summary": page.summary,
                "updated": page.updated,
                "aliases": page.aliases,
            }
            for rel, page in pages.items()
        },
        "graph": {
            "incoming_counts": links["incoming_counts"],
            "outgoing_counts": links["outgoing_counts"],
            "broken_edges": links["broken_edges"],
        },
        "anchors": anchors,
        "bridges": bridges,
        "tag_cohesion": cohesion,
        "surprising_connections": surprising,
        "orphan_adjacent": orphan_adjacent,
        "rough_clusters": clusters,
        "delta": delta,
        "questions": questions,
        "snapshot": snapshot,
        "explainable_item_ids": sorted(item_ids),
        "context": {
            "hot": trim(read_optional(vault / "hot.md"), 1200),
            "agents": trim(read_optional(vault / "AGENTS.md"), 1600),
        },
    }


def anchor_pages(pages: dict[str, lint.Page], links: dict[str, Any]) -> list[dict[str, Any]]:
    incoming = links["incoming_counts"]
    outgoing = links["outgoing_counts"]
    rows = []
    for rel in pages:
        in_count = incoming.get(rel, 0)
        out_count = outgoing.get(rel, 0)
        if in_count > 0 and out_count == 0:
            note = "sink hub - wiki-fixlink candidate"
        elif in_count > 0 and out_count > 0:
            note = "connector hub"
        else:
            note = "low-signal page"
        rows.append({"id": f"anchor:{rel}", "page": rel, "incoming": in_count, "outgoing": out_count, "note": note})
    return sorted(rows, key=lambda item: (-item["incoming"], item["page"]))[:10]


def tag_cohesion(pages: dict[str, lint.Page], links: dict[str, Any]) -> list[dict[str, Any]]:
    by_tag: dict[str, set[str]] = defaultdict(set)
    edge_pairs = {
        frozenset([edge["source"], edge["resolved"]])
        for edge in links["resolved_edges"]
        if edge["source"] != edge["resolved"]
    }
    for page in pages.values():
        for tag in public_tags(page):
            by_tag[tag].add(page.rel)

    rows = []
    for tag, rels in sorted(by_tag.items()):
        n = len(rels)
        if n < 5:
            continue
        actual = sum(1 for edge in edge_pairs if edge.issubset(rels))
        possible = n * (n - 1) / 2
        cohesion = actual / possible if possible else 0.0
        rows.append(
            {
                "id": f"cohesion:{tag}",
                "tag": tag,
                "pages": n,
                "actual_links": actual,
                "possible_links": int(possible),
                "cohesion": round(cohesion, 3),
                "note": "wiki-fixlink target" if cohesion < 0.15 else "well-linked",
            }
        )
    return rows


def bridge_pages(pages: dict[str, lint.Page], links: dict[str, Any]) -> list[dict[str, Any]]:
    neighbors = undirected_neighbors(links)
    rows = []
    for page in pages.values():
        adjacent = sorted(neighbors.get(page.rel, set()))
        if len(adjacent) < 2:
            continue
        pair_count = 0
        tag_pairs: Counter[tuple[str, str]] = Counter()
        for left_index, left in enumerate(adjacent):
            for right in adjacent[left_index + 1 :]:
                if share_public_tag(pages[left], pages[right]):
                    continue
                if connected_within_two_hops(left, right, neighbors, blocked=page.rel):
                    continue
                pair_count += 1
                tag_pairs[ordered_pair(dominant_tag(pages[left]), dominant_tag(pages[right]))] += 1
        if pair_count:
            bridge_labels = [
                f"{left} <-> {right}"
                for (left, right), _count in sorted(tag_pairs.items(), key=lambda item: (-item[1], item[0]))[:3]
            ]
            rows.append(
                {
                    "id": f"bridge:{page.rel}",
                    "page": page.rel,
                    "bridges": bridge_labels,
                    "cross_cluster_pairs": pair_count,
                    "note": "connects otherwise separate tag clusters",
                }
            )
    return sorted(rows, key=lambda item: (-item["cross_cluster_pairs"], item["page"]))[:5]


def surprising_connections(pages: dict[str, lint.Page], links: dict[str, Any]) -> list[dict[str, Any]]:
    incoming = links["incoming_counts"]
    outgoing = links["outgoing_counts"]
    rows = []
    seen: set[tuple[str, str]] = set()
    for edge in links["resolved_edges"]:
        source = edge["source"]
        target = edge["resolved"]
        if source == target or (source, target) in seen:
            continue
        seen.add((source, target))
        source_page = pages[source]
        target_page = pages[target]
        if source_page.category == target_page.category:
            continue
        score = 0
        reasons: list[str] = []
        body = source_page.body.lower()
        if "^[ambiguous]" in body:
            score += 3
            reasons.append("marked ^[ambiguous]")
        if "^[inferred]" in body:
            score += 2
            reasons.append("marked ^[inferred]")
        score += 2
        reasons.append(f"cross-layer ({source_page.category} <-> {target_page.category})")
        if incoming.get(source, 0) + outgoing.get(source, 0) <= 2 and incoming.get(target, 0) >= 8:
            score += 2
            reasons.append("sparse source points to high-incoming target")
        rows.append(
            {
                "id": f"surprising:{source}->{target}",
                "source": source,
                "target": target,
                "score": score,
                "reasons": reasons,
                "note": "; ".join(reasons),
            }
        )
    return sorted(rows, key=lambda item: (-item["score"], item["source"], item["target"]))[:5]


def orphan_adjacent_pages(anchors: list[dict[str, Any]], links: dict[str, Any]) -> list[dict[str, Any]]:
    top_hubs = {item["page"] for item in anchors}
    outgoing_counts = links["outgoing_counts"]
    linked_from_hubs: dict[str, set[str]] = defaultdict(set)
    for edge in links["resolved_edges"]:
        if edge["source"] in top_hubs and outgoing_counts.get(edge["resolved"], 0) == 0:
            linked_from_hubs[edge["resolved"]].add(edge["source"])
    return [
        {
            "id": f"orphan:{page}",
            "page": page,
            "linked_from_hubs": sorted(hubs),
            "hub_count": len(hubs),
            "outgoing": 0,
            "note": "dead-end near a hub",
        }
        for page, hubs in sorted(linked_from_hubs.items(), key=lambda item: (-len(item[1]), item[0]))
    ]


def rough_clusters(anchors: list[dict[str, Any]], pages: dict[str, lint.Page]) -> list[dict[str, Any]]:
    grouped: dict[str, list[str]] = defaultdict(list)
    for anchor in anchors:
        page = pages[anchor["page"]]
        grouped[dominant_tag(page)].append(page.rel)
    rows = []
    for tag, rels in sorted(grouped.items()):
        rows.append({"id": f"cluster:{tag}", "tag": tag, "pages": sorted(rels), "note": "anchor pages grouped by dominant tag"})
    return rows


def suggested_questions(
    pages: dict[str, lint.Page],
    links: dict[str, Any],
    bridges: list[dict[str, Any]],
    cohesion: list[dict[str, Any]],
    orphan_adjacent: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    questions: list[dict[str, Any]] = []
    ambiguous = sorted(page.rel for page in pages.values() if "^[ambiguous]" in page.body.lower())
    for rel in ambiguous[:2]:
        questions.append({"question": f"Resolve: What is still ambiguous on `{strip_md(rel)}`?", "reason": "ambiguous claim"})
    for bridge in bridges[:2]:
        questions.append({"question": f"Explore: Why does `{strip_md(bridge['page'])}` bridge {', '.join(bridge['bridges'])}?", "reason": "bridge node"})
    for item in orphan_adjacent[:2]:
        questions.append({"question": f"Link: `{item['page']}` has no outgoing links; what should it connect to?", "reason": "dead-end near hub"})
    fragmented = sorted((item for item in cohesion if item["cohesion"] < 0.15), key=lambda item: (item["cohesion"], item["tag"]))
    for item in fragmented[:2]:
        questions.append({"question": f"Audit: Should tag `#{item['tag']}` be split or cross-linked?", "reason": "fragmented tag cluster"})

    incoming = links["incoming_counts"]
    for rel in sorted(page for page, count in incoming.items() if count == 0)[:2]:
        questions.append({"question": f"Link: `{rel}` has no incoming links; what should reference it?", "reason": "isolated page"})

    limited = questions[:7]
    for index, item in enumerate(limited, start=1):
        item["id"] = f"question:{index}"
    return limited


def build_snapshot(pages: dict[str, lint.Page], links: dict[str, Any]) -> dict[str, Any]:
    edges = sorted({(edge["source"], edge["resolved"]) for edge in links["resolved_edges"] if edge["source"] != edge["resolved"]})
    return {"nodes": sorted(pages), "edges": [[source, target] for source, target in edges]}


def previous_insights_output(vault: Path) -> Path:
    candidates = [path for path in (vault / "_runs").glob("insights-*/_insights.md") if path.is_file()]
    if candidates:
        return max(candidates, key=lambda path: (path.stat().st_mtime_ns, path.as_posix()))
    return vault / "_insights.md"


def read_previous_snapshot(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    match = re.search(r"<!--\s*GRAPH_SNAPSHOT:\s*(\{.*?\})\s*-->", path.read_text(encoding="utf-8"), re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    nodes = data.get("nodes")
    edges = data.get("edges")
    if not isinstance(nodes, list) or not isinstance(edges, list):
        return None
    return {
        "nodes": sorted(str(node) for node in nodes),
        "edges": sorted([str(edge[0]), str(edge[1])] for edge in edges if isinstance(edge, list) and len(edge) == 2),
    }


def graph_delta(previous: dict[str, Any] | None, current: dict[str, Any]) -> dict[str, Any]:
    current_nodes = set(current["nodes"])
    current_edges = {tuple(edge) for edge in current["edges"]}
    current_incoming = incoming_from_edges(current_edges)
    if previous is None:
        return {
            "has_previous": False,
            "new_pages": sorted(current_nodes),
            "removed_pages": [],
            "new_wikilinks": sorted([list(edge) for edge in current_edges]),
            "removed_wikilinks": [],
            "newly_connected": sorted(page for page, count in current_incoming.items() if count > 0),
            "lost_incoming": [],
        }

    previous_nodes = set(previous["nodes"])
    previous_edges = {tuple(edge) for edge in previous["edges"]}
    previous_incoming = incoming_from_edges(previous_edges)
    return {
        "has_previous": True,
        "new_pages": sorted(current_nodes - previous_nodes),
        "removed_pages": sorted(previous_nodes - current_nodes),
        "new_wikilinks": sorted([list(edge) for edge in current_edges - previous_edges]),
        "removed_wikilinks": sorted([list(edge) for edge in previous_edges - current_edges]),
        "newly_connected": sorted(
            page
            for page in current_nodes
            if previous_incoming.get(page, 0) == 0 and current_incoming.get(page, 0) > 0
        ),
        "lost_incoming": sorted(
            {
                page
                for page in previous_nodes | current_nodes
                if previous_incoming.get(page, 0) > current_incoming.get(page, 0)
            }
        ),
    }


def run_explanation_enrichment(args: Any, vault: Path, run: Run) -> dict[str, Any]:
    codex_bin = shutil.which(getattr(args, "codex_bin", "codex"))
    if codex_bin is None:
        return {"status": "unavailable", "explanations": {}, "warnings": ["Codex executable not found; using deterministic explanations"]}
    command = [
        codex_bin,
        "exec",
        "--sandbox",
        getattr(args, "sandbox", "workspace-write"),
        "--cd",
        str(vault),
        "--add-dir",
        str(run.run_dir.resolve()),
    ]
    for directory in getattr(args, "add_dir", []) or []:
        command.extend(["--add-dir", str(Path(directory).expanduser().resolve())])
    command.append(build_prompt(vault, run.packet_path, run.explanations_path))
    result = subprocess.call(command, cwd=vault, env=qmd_env(os.environ, vault))
    if result != 0:
        return {"status": "failed", "explanations": {}, "warnings": [f"Codex explanation enrichment exited with {result}"]}
    return load_explanations(run.explanations_path, run.packet_path)


def build_prompt(vault: Path, packet_path: Path, explanations_path: Path) -> str:
    return (
        "Use the `wiki-insights` skill as a bounded explanation layer for this deterministic insights packet.\n\n"
        f"Vault path: {vault}\n"
        f"Deterministic packet path: {packet_path}\n"
        f"Write explanation JSON to: {explanations_path}\n\n"
        "Read the deterministic packet. Do not modify wiki files, do not change graph facts, and do not invent "
        "rankings, counts, pages, tags, or links. Write exactly one JSON object with this shape:\n"
        "{\n"
        '  "version": 1,\n'
        '  "status": "completed",\n'
        '  "explanations": [{"id": "anchor:concepts/example.md", "explanation": "Short explanation."}],\n'
        '  "warnings": []\n'
        "}\n"
        f"Only use ids listed in `explainable_item_ids`. Each explanation must be {MAX_EXPLANATION_CHARS} characters or less."
    )


def load_explanations(path: Path, packet_path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        packet = json.loads(packet_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return {"status": "invalid", "explanations": {}, "warnings": [f"could not read explanation JSON: {exc}"]}
    allowed = set(packet.get("explainable_item_ids", []))
    warnings: list[str] = []
    explanations: dict[str, str] = {}
    items = raw.get("explanations") if isinstance(raw, dict) else None
    if not isinstance(items, list):
        return {"status": "invalid", "explanations": {}, "warnings": ["explanation JSON must contain an explanations list"]}
    for item in items:
        if not isinstance(item, dict):
            warnings.append("ignored non-object explanation item")
            continue
        item_id = str(item.get("id") or "")
        explanation = str(item.get("explanation") or "").strip()
        if item_id not in allowed:
            warnings.append(f"ignored explanation for unknown id: {item_id}")
            continue
        if not explanation:
            continue
        explanations[item_id] = explanation[:MAX_EXPLANATION_CHARS]
    return {
        "status": str(raw.get("status") or "completed") if isinstance(raw, dict) else "invalid",
        "explanations": explanations,
        "warnings": [*warnings, *[str(item) for item in raw.get("warnings", []) if str(item)]],
    }


def render_insights_markdown(packet: dict[str, Any], explanation_result: dict[str, Any]) -> str:
    explanations = explanation_result.get("explanations") if isinstance(explanation_result.get("explanations"), dict) else {}
    lines = [
        "---",
        "title: Wiki Insights",
        f"tags: {managed_tags()}",
        f"updated: {packet['generated_at']}",
        "---",
        "",
        f"# Wiki Insights - {packet['generated_at']}",
        "",
        "## Anchor Pages (top 10 hubs)",
        "| Page | Incoming | Outgoing | Note |",
        "|---|---:|---:|---|",
    ]
    for item in packet["anchors"]:
        note = explanations.get(item["id"]) or item["note"]
        lines.append(f"| {wiki_link(item['page'])} | {item['incoming']} | {item['outgoing']} | {escape_table(note)} |")

    lines.extend(["", "## Bridge Pages (top 5)", "| Page | Bridges | Cross-cluster pairs |", "|---|---|---:|"])
    for item in packet["bridges"] or []:
        note = explanations.get(item["id"]) or ", ".join(item["bridges"])
        lines.append(f"| {wiki_link(item['page'])} | {escape_table(note)} | {item['cross_cluster_pairs']} |")
    if not packet["bridges"]:
        lines.append("| - | - | 0 |")

    cohesive = sorted(packet["tag_cohesion"], key=lambda item: (-item["cohesion"], item["tag"]))[:5]
    fragmented = sorted(packet["tag_cohesion"], key=lambda item: (item["cohesion"], item["tag"]))[:5]
    lines.extend(["", "## Tag Cluster Cohesion", "### Most cohesive (well-linked)"])
    lines.extend(render_cohesion_item(item, explanations) for item in cohesive)
    if not cohesive:
        lines.append("- None.")
    lines.append("### Most fragmented (wiki-fixlink targets)")
    lines.extend(render_cohesion_item(item, explanations) for item in fragmented)
    if not fragmented:
        lines.append("- None.")

    lines.extend(["", "## Surprising Connections (top 5)"])
    for item in packet["surprising_connections"]:
        reason = explanations.get(item["id"]) or item["note"]
        lines.append(f"- {wiki_link(item['source'])} -> {wiki_link(item['target'])} - score {item['score']}")
        lines.append(f"  - Reason: {reason}")
    if not packet["surprising_connections"]:
        lines.append("- None.")

    lines.extend(["", "## Orphan-Adjacent (dead-ends near hubs)"])
    for item in packet["orphan_adjacent"]:
        note = explanations.get(item["id"]) or f"linked from {item['hub_count']} hubs, 0 outbound links"
        lines.append(f"- {wiki_link(item['page'])} - {note}")
    if not packet["orphan_adjacent"]:
        lines.append("- None.")

    lines.extend(["", "## Rough Clusters"])
    for item in packet["rough_clusters"]:
        pages = ", ".join(strip_md(page).split("/", 1)[-1] for page in item["pages"])
        note = explanations.get(item["id"])
        suffix = f" - {note}" if note else ""
        lines.append(f"- **#{item['tag']}** - {pages}{suffix}")
    if not packet["rough_clusters"]:
        lines.append("- None.")

    lines.extend(["", "## Graph Delta Since Last Run"])
    lines.extend(render_delta(packet["delta"]))

    lines.extend(["", "## Questions Worth Asking"])
    for index, item in enumerate(packet["questions"], start=1):
        question = explanations.get(item["id"]) or item["question"]
        lines.append(f"{index}. {question} ({item['reason']})")
    if not packet["questions"]:
        lines.append("1. No structure-derived questions yet.")

    if explanation_result.get("status") and explanation_result.get("status") not in {"completed", "not_run"}:
        lines.extend(["", "## Explanation Enrichment", f"- Status: {explanation_result['status']}"])
    snapshot_json = json.dumps(packet["snapshot"], separators=(",", ":"), sort_keys=True)
    lines.extend(["", f"<!-- GRAPH_SNAPSHOT: {snapshot_json} -->", ""])
    return "\n".join(lines)


def render_cohesion_item(item: dict[str, Any], explanations: dict[str, str]) -> str:
    note = explanations.get(item["id"]) or item["note"]
    return f"- **#{item['tag']}** - {item['pages']} pages, cohesion {item['cohesion']:.2f} - {note}"


def render_delta(delta: dict[str, Any]) -> list[str]:
    if not delta["has_previous"]:
        return [
            "- No previous graph snapshot; this run establishes the baseline.",
            f"- Baseline: {len(delta['new_pages'])} pages, {len(delta['new_wikilinks'])} wikilinks",
        ]
    lines = [
        f"- +{len(delta['new_pages'])} new pages, -{len(delta['removed_pages'])} removed pages",
        f"- +{len(delta['new_wikilinks'])} new wikilinks, -{len(delta['removed_wikilinks'])} removed wikilinks",
    ]
    if delta["newly_connected"]:
        lines.append("- Newly connected: " + ", ".join(wiki_link(page) for page in delta["newly_connected"][:10]))
    if delta["lost_incoming"]:
        lines.append("- Lost incoming links: " + ", ".join(wiki_link(page) for page in delta["lost_incoming"][:10]))
    return lines


def append_log(vault: Path, packet: dict[str, Any]) -> None:
    summary = packet["summary"]
    delta = packet["delta"]
    line = (
        f"- [{now_iso()}] WIKI_INSIGHTS anchors={summary['anchors']} bridges={summary['bridges']} "
        f"cohesion_checked={summary['cohesion_checked']} surprising={summary['surprising']} "
        f"questions={summary['questions']} delta=\"+{len(delta['new_pages'])} pages +{len(delta['new_wikilinks'])} links\"\n"
    )
    path = vault / "log.md"
    if path.exists():
        ensure_managed_tag(path, "Wiki Log")
        path.write_text(path.read_text(encoding="utf-8").rstrip() + "\n" + line, encoding="utf-8")
    else:
        path.write_text(f"---\ntitle: Wiki Log\ntags: {managed_tags()}\n---\n\n# Wiki Log\n\n" + line, encoding="utf-8")


def build_report(
    packet: dict[str, Any],
    *,
    status: str,
    explanation_status: str = "not_run",
    warnings: list[str] | None = None,
    output_path: str = "",
) -> dict[str, Any]:
    return {
        "version": 1,
        "status": status,
        "generated_at": now_iso(),
        "vault": packet.get("vault"),
        "output_path": output_path,
        "explanation_status": explanation_status,
        "summary": packet.get("summary", {}),
        "delta": packet.get("delta", {}),
        "warnings": warnings or [],
    }


def print_report(report: dict[str, Any], *, json_output: bool) -> None:
    if json_output:
        print(json.dumps(report, indent=2, sort_keys=True))
        return
    summary = report["summary"]
    lines = [
        "## Wiki Insights Report",
        "",
        f"- **Status:** {report['status']}",
        f"- **Pages scanned:** {summary.get('pages_scanned', 0)}",
        f"- **Anchors:** {summary.get('anchors', 0)}",
        f"- **Bridges:** {summary.get('bridges', 0)}",
        f"- **Surprising connections:** {summary.get('surprising', 0)}",
        f"- **Questions:** {summary.get('questions', 0)}",
        f"- **Explanation enrichment:** {report.get('explanation_status', 'not_run')}",
    ]
    if report.get("output_path"):
        lines.append(f"- **Output:** {report['output_path']}")
    if report.get("warnings"):
        lines.extend(["", "### Warnings", ""])
        lines.extend(f"- {warning}" for warning in report["warnings"])
    print("\n".join(lines).rstrip() + "\n")


def create_run(vault: Path) -> Run:
    candidate = timestamped_run_dir(vault, "insights")
    return Run(
        run_dir=candidate,
        packet_path=candidate / "packet.json",
        explanations_path=candidate / "explanations.json",
        output_path=candidate / "_insights.md",
        report_path=candidate / "report.json",
    )


def insight_item_ids(
    anchors: list[dict[str, Any]],
    bridges: list[dict[str, Any]],
    cohesion: list[dict[str, Any]],
    surprising: list[dict[str, Any]],
    orphan_adjacent: list[dict[str, Any]],
    clusters: list[dict[str, Any]],
    questions: list[dict[str, Any]],
) -> set[str]:
    ids: set[str] = set()
    for group in [anchors, bridges, cohesion, surprising, orphan_adjacent, clusters, questions]:
        ids.update(str(item["id"]) for item in group if item.get("id"))
    return ids


def undirected_neighbors(links: dict[str, Any]) -> dict[str, set[str]]:
    neighbors: dict[str, set[str]] = defaultdict(set)
    for edge in links["resolved_edges"]:
        source = edge["source"]
        target = edge["resolved"]
        if source == target:
            continue
        neighbors[source].add(target)
        neighbors[target].add(source)
    return neighbors


def connected_within_two_hops(start: str, goal: str, neighbors: dict[str, set[str]], *, blocked: str) -> bool:
    queue: deque[tuple[str, int]] = deque([(start, 0)])
    seen = {start, blocked}
    while queue:
        current, depth = queue.popleft()
        if depth >= 2:
            continue
        for next_node in neighbors.get(current, set()):
            if next_node == goal:
                return True
            if next_node in seen:
                continue
            seen.add(next_node)
            queue.append((next_node, depth + 1))
    return False


def incoming_from_edges(edges: set[tuple[str, str]]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for _source, target in edges:
        counts[target] += 1
    return counts


def public_tags(page: lint.Page) -> list[str]:
    tags = [tag.lstrip("#") for tag in page.tags if tag]
    return sorted(tag for tag in tags if not tag.startswith("visibility/"))


def dominant_tag(page: lint.Page) -> str:
    tags = public_tags(page)
    return tags[0] if tags else page.category


def share_public_tag(left: lint.Page, right: lint.Page) -> bool:
    return bool(set(public_tags(left)).intersection(public_tags(right)))


def ordered_pair(left: str, right: str) -> tuple[str, str]:
    return (left, right) if left <= right else (right, left)


def wiki_link(rel: str) -> str:
    return f"[[{strip_md(rel)}]]"


def strip_md(rel: str) -> str:
    return rel[:-3] if rel.endswith(".md") else rel


def escape_table(value: str) -> str:
    return value.replace("|", "\\|")


def trim(value: str, max_chars: int) -> str:
    text = value.strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 15].rstrip() + "\n[... truncated]"


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
