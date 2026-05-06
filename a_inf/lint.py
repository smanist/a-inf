from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from a_inf.ingest import parse_datetime, parse_frontmatter_file, write_json
from a_inf.managed_files import A_INF_TAG, ensure_managed_tag, managed_tags


CONTENT_DIRS = ["concepts", "entities", "skills", "references", "synthesis", "journal", "projects", "misc"]
REQUIRED_FRONTMATTER = {"title", "category", "tags", "sources", "created", "updated"}
VALID_LIFECYCLES = {"draft", "reviewed", "verified", "disputed", "archived"}
SEMANTIC_REVIEW_KEYS = {"status", "scope", "findings", "repair_recommendations", "reviewed_candidate_ids", "warnings"}


@dataclass(frozen=True)
class Page:
    path: Path
    rel: str
    stem: str
    title: str
    category: str
    tags: list[str]
    sources: list[str]
    summary: str
    aliases: list[str]
    updated: str
    lifecycle: str
    base_confidence: Any
    superseded_by: str
    frontmatter: dict[str, Any]
    body: str


def run_lint(args: Any, vault: Path, config: dict[str, str]) -> int:
    packet = build_lint_packet(vault, config)
    run_dir = create_lint_run_dir(vault)
    packet_path = run_dir / "packet.json"
    review_path = run_dir / "semantic_review.json"
    write_json(packet_path, packet)

    if getattr(args, "print_prompt", False):
        print(build_semantic_prompt(vault, packet_path, review_path, getattr(args, "semantic_scope", "one-hop")))
        return 0

    if getattr(args, "no_codex", False):
        packet["semantic_review"] = semantic_status("not_run", getattr(args, "semantic_scope", "one-hop"), ["semantic review skipped by --no-codex"])
    else:
        packet["semantic_review"] = run_semantic_review(args, vault, packet_path, review_path)

    if not getattr(args, "no_log", False):
        append_lint_log(vault, packet)

    if getattr(args, "json", False):
        print(json.dumps(packet, indent=2, sort_keys=True))
    else:
        print(render_lint_markdown(packet))
    return 0


def build_lint_packet(vault: Path, config: dict[str, str] | None = None) -> dict[str, Any]:
    generated_at = now_iso()
    pages = build_page_registry(vault)
    links = build_link_graph(pages)
    findings = build_findings(vault, pages, links)
    candidates = build_candidates(pages, links)
    summary = summarize_findings(findings, candidates)
    return {
        "version": 1,
        "generated_at": generated_at,
        "vault": str(vault),
        "summary": summary,
        "page_registry": {
            page.rel: {
                "title": page.title,
                "category": page.category,
                "tags": page.tags,
                "summary": page.summary,
                "updated": page.updated,
                "lifecycle": page.lifecycle,
                "base_confidence": page.base_confidence,
                "aliases": page.aliases,
            }
            for page in pages.values()
        },
        "graph": {
            "resolved_edges": links["resolved_edges"],
            "broken_edges": links["broken_edges"],
            "incoming_counts": links["incoming_counts"],
            "outgoing_counts": links["outgoing_counts"],
        },
        "findings": findings,
        "candidates": candidates,
        "semantic_review": semantic_status("pending", "one-hop", []),
    }


def build_page_registry(vault: Path) -> dict[str, Page]:
    pages: dict[str, Page] = {}
    for rel in iter_content_markdown(vault):
        path = vault / rel
        text = read_text(path)
        fm = parse_frontmatter_file(path)
        body = strip_frontmatter(text)
        tags = as_text_list(fm.get("tags")) or read_frontmatter_sequence(path, "tags")
        sources = as_text_list(fm.get("sources")) or read_frontmatter_sequence(path, "sources")
        aliases = as_text_list(fm.get("aliases")) or read_frontmatter_sequence(path, "aliases")
        title = str(fm.get("title") or path.stem)
        category = str(fm.get("category") or rel.split("/", 1)[0])
        pages[rel] = Page(
            path=path,
            rel=rel,
            stem=path.stem,
            title=title,
            category=category,
            tags=tags,
            sources=sources,
            summary=str(fm.get("summary") or ""),
            aliases=aliases,
            updated=str(fm.get("updated") or ""),
            lifecycle=str(fm.get("lifecycle") or ""),
            base_confidence=fm.get("base_confidence"),
            superseded_by=str(fm.get("superseded_by") or ""),
            frontmatter=fm,
            body=body,
        )
    return pages


def iter_content_markdown(vault: Path) -> Iterable[str]:
    for dirname in CONTENT_DIRS:
        root = vault / dirname
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.md")):
            if any(part in {"_archives", "_raw", ".obsidian", "node_modules"} for part in path.parts):
                continue
            yield path.relative_to(vault).as_posix()


def build_link_graph(pages: dict[str, Page]) -> dict[str, Any]:
    resolver = LinkResolver(pages)
    resolved_edges: list[dict[str, Any]] = []
    broken_edges: list[dict[str, Any]] = []
    incoming: dict[str, int] = {rel: 0 for rel in pages}
    outgoing: dict[str, int] = {rel: 0 for rel in pages}
    for page in pages.values():
        seen_targets: set[str] = set()
        for link in extract_wikilinks(page.body):
            target = resolver.resolve(link["target"])
            edge = {"source": page.rel, "target": link["target"], "line": link["line"], "raw": link["raw"]}
            if target:
                edge["resolved"] = target
                resolved_edges.append(edge)
                if target not in seen_targets:
                    incoming[target] += 1
                    outgoing[page.rel] += 1
                    seen_targets.add(target)
            else:
                broken_edges.append(edge)
    return {
        "resolved_edges": resolved_edges,
        "broken_edges": broken_edges,
        "incoming_counts": incoming,
        "outgoing_counts": outgoing,
    }


class LinkResolver:
    def __init__(self, pages: dict[str, Page]) -> None:
        self.pages = pages
        self.by_no_ext = {rel[:-3]: rel for rel in pages if rel.endswith(".md")}
        stems: dict[str, list[str]] = defaultdict(list)
        titles: dict[str, list[str]] = defaultdict(list)
        aliases: dict[str, list[str]] = defaultdict(list)
        for page in pages.values():
            stems[normalize_slug(page.stem)].append(page.rel)
            titles[normalize_slug(page.title)].append(page.rel)
            for alias in page.aliases:
                aliases[normalize_slug(alias)].append(page.rel)
        self.stems = stems
        self.titles = titles
        self.aliases = aliases

    def resolve(self, target: str) -> str | None:
        cleaned = clean_link_target(target)
        if not cleaned:
            return None
        candidates = [cleaned]
        if cleaned.endswith(".md"):
            candidates.append(cleaned[:-3])
        else:
            candidates.append(f"{cleaned}.md")
        for candidate in candidates:
            if candidate in self.pages:
                return candidate
            if candidate in self.by_no_ext:
                return self.by_no_ext[candidate]
        normalized = normalize_slug(Path(cleaned).stem)
        for lookup in [self.stems, self.titles, self.aliases]:
            matches = lookup.get(normalized) or []
            if len(matches) == 1:
                return matches[0]
        return None


def build_findings(vault: Path, pages: dict[str, Page], links: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    findings = {
        "orphaned_pages": find_orphans(pages, links),
        "broken_wikilinks": find_broken_wikilinks(links),
        "missing_frontmatter": find_missing_frontmatter(pages),
        "missing_summary": find_missing_summaries(pages),
        "stale_content": find_stale_content(pages),
        "index_issues": find_index_issues(vault, pages),
        "provenance_issues": find_provenance_issues(pages, links),
        "fragmented_tag_clusters": find_fragmented_tag_clusters(pages, links),
        "visibility_issues": find_visibility_issues(vault, pages),
        "misc_promotion_candidates": find_misc_promotion_candidates(pages, links),
        "lifecycle_confidence_issues": find_lifecycle_confidence_issues(pages, links),
    }
    return findings


def find_orphans(pages: dict[str, Page], links: dict[str, Any]) -> list[dict[str, Any]]:
    incoming = links["incoming_counts"]
    return [
        {"page": rel, "incoming": incoming.get(rel, 0), "message": "no incoming wikilinks"}
        for rel in sorted(pages)
        if incoming.get(rel, 0) == 0 and not has_a_inf_tag(pages[rel])
    ]


def has_a_inf_tag(page: Page) -> bool:
    return A_INF_TAG in {tag.lstrip("#") for tag in page.tags}


def find_broken_wikilinks(links: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "page": edge["source"],
            "line": edge["line"],
            "target": edge["target"],
            "raw": edge["raw"],
            "message": "wikilink target does not resolve to an existing page",
        }
        for edge in links["broken_edges"]
    ]


def find_missing_frontmatter(pages: dict[str, Page]) -> list[dict[str, Any]]:
    issues = []
    for page in pages.values():
        missing = sorted(REQUIRED_FRONTMATTER - set(page.frontmatter))
        if missing:
            issues.append({"page": page.rel, "missing": missing, "message": f"missing: {', '.join(missing)}"})
    return issues


def find_missing_summaries(pages: dict[str, Page]) -> list[dict[str, Any]]:
    issues = []
    for page in pages.values():
        if not page.summary:
            issues.append({"page": page.rel, "message": "missing summary field"})
        elif len(page.summary) > 200:
            issues.append({"page": page.rel, "length": len(page.summary), "message": "summary exceeds 200 characters"})
    return issues


def find_stale_content(pages: dict[str, Page]) -> list[dict[str, Any]]:
    now = datetime.now(timezone.utc)
    issues = []
    for page in pages.values():
        updated = parse_datetime(page.updated)
        if not updated:
            continue
        age_days = (now - updated).days
        if age_days > 90:
            issues.append(
                {
                    "page": page.rel,
                    "updated": page.updated,
                    "age_days": age_days,
                    "lifecycle": page.lifecycle or None,
                    "priority": "high" if page.lifecycle == "verified" else "normal",
                    "message": "stale verified page" if page.lifecycle == "verified" else "stale page",
                }
            )
    return issues


def find_index_issues(vault: Path, pages: dict[str, Page]) -> list[dict[str, Any]]:
    index_path = vault / "index.md"
    if not index_path.exists():
        return [{"page": "index.md", "message": "index.md is missing"}]
    text = read_text(index_path)
    resolver = LinkResolver(pages)
    linked_pages = {resolved for link in extract_wikilinks(text) if (resolved := resolver.resolve(link["target"]))}
    issues = []
    for rel in sorted(set(pages) - linked_pages):
        issues.append({"page": rel, "message": "page exists on disk but is not listed in index.md"})
    for link in extract_wikilinks(text):
        if not resolver.resolve(link["target"]):
            issues.append(
                {
                    "page": "index.md",
                    "line": link["line"],
                    "target": link["target"],
                    "message": "index.md links to a missing page",
                }
            )
    return issues


def find_provenance_issues(pages: dict[str, Page], links: dict[str, Any]) -> list[dict[str, Any]]:
    issues = []
    top_hubs = {
        rel
        for rel, _count in sorted(links["incoming_counts"].items(), key=lambda item: item[1], reverse=True)[:10]
    }
    for page in pages.values():
        counts = count_provenance(page.body)
        provenance = page.frontmatter.get("provenance")
        if counts["ambiguous"] > 0.15:
            issues.append({"page": page.rel, "ambiguous": counts["ambiguous"], "message": "AMBIGUOUS > 15%"})
        if counts["inferred"] > 0.40 and not page.sources:
            issues.append({"page": page.rel, "inferred": counts["inferred"], "message": "unsourced synthesis"})
        if page.rel in top_hubs and counts["inferred"] > 0.20:
            issues.append({"page": page.rel, "inferred": counts["inferred"], "message": "hub page with INFERRED > 20%"})
        if isinstance(provenance, dict):
            for key in ["extracted", "inferred", "ambiguous"]:
                stored = provenance.get(key)
                if isinstance(stored, (int, float)) and abs(float(stored) - counts[key]) > 0.20:
                    issues.append(
                        {
                            "page": page.rel,
                            "field": key,
                            "stored": stored,
                            "computed": counts[key],
                            "message": f"provenance drift for {key}",
                        }
                    )
    return issues


def find_fragmented_tag_clusters(pages: dict[str, Page], links: dict[str, Any]) -> list[dict[str, Any]]:
    by_tag: dict[str, set[str]] = defaultdict(set)
    edge_pairs = {
        frozenset([edge["source"], edge["resolved"]])
        for edge in links["resolved_edges"]
        if edge["source"] != edge["resolved"]
    }
    for page in pages.values():
        for tag in page.tags:
            if not tag.startswith("visibility/"):
                by_tag[tag].add(page.rel)
    issues = []
    for tag, rels in sorted(by_tag.items()):
        n = len(rels)
        if n < 5:
            continue
        actual = sum(1 for edge in edge_pairs if edge.issubset(rels))
        possible = n * (n - 1) / 2
        cohesion = actual / possible if possible else 0.0
        if cohesion < 0.15:
            issues.append(
                {
                    "tag": tag,
                    "pages": n,
                    "actual_links": actual,
                    "cohesion": round(cohesion, 3),
                    "message": "fragmented tag cluster; run a-inf fixlink",
                }
            )
    return issues


def find_visibility_issues(vault: Path, pages: dict[str, Page]) -> list[dict[str, Any]]:
    issues = []
    pii_pattern = re.compile(r"\b(password|api_key|secret|token|ssn|email|phone):\s*\S+", re.IGNORECASE)
    for page in pages.values():
        visibility_tags = [tag for tag in page.tags if tag.startswith("visibility/")]
        if pii_pattern.search(page.body) and not {"visibility/pii", "visibility/internal"}.intersection(visibility_tags):
            issues.append({"page": page.rel, "message": "contains likely sensitive value but lacks visibility tag"})
        if "visibility/pii" in visibility_tags and "sources" not in page.frontmatter:
            issues.append({"page": page.rel, "message": "visibility/pii page is missing sources frontmatter"})
    taxonomy = vault / "_meta" / "taxonomy.md"
    if taxonomy.exists():
        for line_no, line in enumerate(read_text(taxonomy).splitlines(), start=1):
            if "visibility/" in line:
                issues.append(
                    {
                        "page": "_meta/taxonomy.md",
                        "line": line_no,
                        "message": "visibility tags are system tags and should not be in taxonomy",
                    }
                )
    return issues


def find_misc_promotion_candidates(pages: dict[str, Page], links: dict[str, Any]) -> list[dict[str, Any]]:
    neighbors: dict[str, set[str]] = defaultdict(set)
    for edge in links["resolved_edges"]:
        neighbors[edge["source"]].add(edge["resolved"])
        neighbors[edge["resolved"]].add(edge["source"])
    issues = []
    for page in pages.values():
        if not (page.rel.startswith("misc/") or page.frontmatter.get("promotion_status") == "misc"):
            continue
        scores: dict[str, int] = defaultdict(int)
        for neighbor in neighbors.get(page.rel, set()):
            project = project_for_page(neighbor, pages.get(neighbor))
            if project:
                scores[project] += 1
        if scores:
            project, score = max(scores.items(), key=lambda item: item[1])
            if score >= 3:
                issues.append({"page": page.rel, "top_project": project, "affinity_score": score})
    return issues


def find_lifecycle_confidence_issues(pages: dict[str, Page], links: dict[str, Any]) -> list[dict[str, Any]]:
    resolver = LinkResolver(pages)
    issues = []
    for page in pages.values():
        if not page.lifecycle:
            issues.append({"page": page.rel, "field": "lifecycle", "message": "missing lifecycle field"})
        elif page.lifecycle not in VALID_LIFECYCLES:
            issues.append({"page": page.rel, "field": "lifecycle", "value": page.lifecycle, "message": "invalid lifecycle"})
        confidence = page.base_confidence
        if confidence is None:
            issues.append({"page": page.rel, "field": "base_confidence", "message": "missing base_confidence field"})
        elif not isinstance(confidence, (int, float)) or not 0 <= float(confidence) <= 1:
            issues.append({"page": page.rel, "field": "base_confidence", "value": confidence, "message": "out of range [0.0, 1.0]"})
        if page.superseded_by:
            target = resolver.resolve(page.superseded_by)
            if not target:
                issues.append({"page": page.rel, "field": "superseded_by", "target": page.superseded_by, "message": "supersession target does not exist"})
            elif pages[target].lifecycle == "archived":
                issues.append({"page": page.rel, "field": "superseded_by", "target": target, "message": "supersession target is archived"})
            if page.lifecycle != "archived":
                issues.append({"page": page.rel, "field": "superseded_by", "message": "superseded page should have lifecycle=archived"})
    issues.extend(find_supersession_cycles(pages, resolver))
    return issues


def find_supersession_cycles(pages: dict[str, Page], resolver: LinkResolver) -> list[dict[str, Any]]:
    edges = {page.rel: resolver.resolve(page.superseded_by) for page in pages.values() if page.superseded_by}
    issues = []
    for start in sorted(edges):
        seen: list[str] = []
        current: str | None = start
        while current and current in edges:
            if current in seen:
                cycle = seen[seen.index(current) :] + [current]
                issues.append({"page": start, "field": "superseded_by", "cycle": cycle, "message": "supersession cycle"})
                break
            seen.append(current)
            current = edges.get(current)
    return issues


def build_candidates(pages: dict[str, Page], links: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    return {
        "contradiction_candidates": find_contradiction_candidates(pages, links),
        "synthesis_gap_candidates": find_synthesis_gap_candidates(pages, links),
    }


def find_contradiction_candidates(pages: dict[str, Page], links: dict[str, Any]) -> list[dict[str, Any]]:
    marked = [
        page
        for page in pages.values()
        if "^[ambiguous]" in page.body.lower()
        or re.search(r"\b(however|in contrast|despite|contradict|conflict)\b", page.body, re.IGNORECASE)
    ]
    candidates = []
    for i, left in enumerate(marked):
        for right in marked[i + 1 :]:
            shared_tags = sorted(set(left.tags).intersection(right.tags) - {tag for tag in left.tags if tag.startswith("visibility/")})
            if not shared_tags:
                continue
            candidates.append(
                {
                    "id": f"contradiction-{len(candidates) + 1}",
                    "pages": [left.rel, right.rel],
                    "shared_tags": shared_tags,
                    "evidence": "ambiguous or contrastive language on related pages",
                    "score": 2 + len(shared_tags),
                }
            )
    return sorted(candidates, key=lambda item: (-item["score"], item["pages"]))[:20]


def find_synthesis_gap_candidates(pages: dict[str, Page], links: dict[str, Any]) -> list[dict[str, Any]]:
    interesting = {rel for rel, page in pages.items() if page.rel.startswith(("concepts/", "entities/"))}
    outgoing_by_source: dict[str, set[str]] = defaultdict(set)
    for edge in links["resolved_edges"]:
        if edge["resolved"] in interesting:
            outgoing_by_source[edge["source"]].add(edge["resolved"])
    cooccurs: dict[frozenset[str], set[str]] = defaultdict(set)
    for source, targets in outgoing_by_source.items():
        ordered = sorted(targets)
        for i, left in enumerate(ordered):
            for right in ordered[i + 1 :]:
                cooccurs[frozenset([left, right])].add(source)
    covered = existing_synthesis_pairs(pages, links)
    candidates = []
    for pair, sources in cooccurs.items():
        if len(sources) < 3 or pair in covered:
            continue
        left, right = sorted(pair)
        score = len(sources) + (2 if pages[left].category != pages[right].category else 0)
        candidates.append(
            {
                "id": f"synthesis-gap-{len(candidates) + 1}",
                "pair": [left, right],
                "cooccurrence": len(sources),
                "source_pages": sorted(sources),
                "score": score,
                "suggested_action": "run wiki-synthesize",
            }
        )
    return sorted(candidates, key=lambda item: (-item["score"], item["pair"]))[:20]


def existing_synthesis_pairs(pages: dict[str, Page], links: dict[str, Any]) -> set[frozenset[str]]:
    by_source: dict[str, set[str]] = defaultdict(set)
    for edge in links["resolved_edges"]:
        by_source[edge["source"]].add(edge["resolved"])
    covered = set()
    for page in pages.values():
        if not page.rel.startswith("synthesis/"):
            continue
        targets = sorted(rel for rel in by_source.get(page.rel, set()) if rel.startswith(("concepts/", "entities/")))
        for i, left in enumerate(targets):
            for right in targets[i + 1 :]:
                covered.add(frozenset([left, right]))
    return covered


def summarize_findings(findings: dict[str, list[dict[str, Any]]], candidates: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    total = sum(len(items) for items in findings.values())
    return {
        "issues_found": total,
        "hard_findings": {key: len(value) for key, value in findings.items()},
        "semantic_candidates": {key: len(value) for key, value in candidates.items()},
    }


def run_semantic_review(args: Any, vault: Path, packet_path: Path, review_path: Path) -> dict[str, Any]:
    scope = getattr(args, "semantic_scope", "one-hop")
    prompt = build_semantic_prompt(vault, packet_path, review_path, scope)
    codex_bin = shutil.which(getattr(args, "codex_bin", "codex"))
    if codex_bin is None:
        print("Codex executable not found. Semantic review skipped.", file=sys.stderr)
        return semantic_status("unavailable", scope, ["Codex executable not found"])
    command = [codex_bin, "exec", "--sandbox", getattr(args, "sandbox", "workspace-write"), "--cd", str(vault)]
    command.extend(["--add-dir", str(packet_path.parent.resolve())])
    for directory in getattr(args, "add_dir", []) or []:
        command.extend(["--add-dir", str(Path(directory).expanduser().resolve())])
    command.append(prompt)
    result = subprocess.call(command, cwd=vault, env=os.environ.copy())
    if result != 0:
        return semantic_status("failed", scope, [f"Codex semantic review exited with {result}"])
    review, warnings = load_semantic_review(review_path, scope)
    if warnings:
        review = semantic_status("invalid", scope, warnings)
    return review


def build_semantic_prompt(vault: Path, packet_path: Path, review_path: Path, scope: str) -> str:
    if scope == "broad":
        scope_text = (
            "You may follow the wiki-lint retrieval-primitives workflow broadly across the vault when needed. "
            "Use targeted greps and section reads before whole-page reads."
        )
    else:
        scope_text = (
            "You may read pages directly referenced by contradiction/synthesis candidates and their one-hop "
            "wikilink neighbors. Do not roam beyond that set."
        )
    return (
        "Use the `wiki-lint` skill as a semantic review layer for this deterministic lint packet.\n\n"
        f"Vault path: {vault}\n"
        f"Deterministic packet path: {packet_path}\n"
        f"Write semantic review JSON to: {review_path}\n"
        f"Semantic scope: {scope}\n"
        f"{scope_text}\n\n"
        "Read the deterministic packet. Do not modify or overwrite it. Promote only well-supported semantic "
        "candidate issues into actual findings. Write exactly one JSON object with this shape:\n"
        "{\n"
        '  "status": "completed",\n'
        f'  "scope": "{scope}",\n'
        '  "findings": {"contradictions": [], "synthesis_gaps": []},\n'
        '  "repair_recommendations": [],\n'
        '  "reviewed_candidate_ids": [],\n'
        '  "warnings": []\n'
        "}\n"
        "Each finding must include the candidate id when it came from a candidate, the involved page paths, "
        "a concise explanation, and the evidence used."
    )


def load_semantic_review(path: Path, expected_scope: str) -> tuple[dict[str, Any], list[str]]:
    try:
        review = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return {}, [f"could not read semantic_review.json: {exc}"]
    warnings = validate_semantic_review(review, expected_scope)
    return review if isinstance(review, dict) else {}, warnings


def validate_semantic_review(review: Any, expected_scope: str) -> list[str]:
    if not isinstance(review, dict):
        return ["semantic_review must be a JSON object"]
    warnings = []
    missing = sorted(SEMANTIC_REVIEW_KEYS - set(review))
    if missing:
        warnings.append(f"semantic_review missing keys: {', '.join(missing)}")
    if review.get("scope") != expected_scope:
        warnings.append(f"semantic_review scope {review.get('scope')!r} did not match {expected_scope!r}")
    findings = review.get("findings")
    if not isinstance(findings, dict):
        warnings.append("semantic_review.findings must be an object")
    else:
        for key in ["contradictions", "synthesis_gaps"]:
            if not isinstance(findings.get(key), list):
                warnings.append(f"semantic_review.findings.{key} must be a list")
    for key in ["repair_recommendations", "reviewed_candidate_ids", "warnings"]:
        if key in review and not isinstance(review.get(key), list):
            warnings.append(f"semantic_review.{key} must be a list")
    return warnings


def semantic_status(status: str, scope: str, warnings: list[str]) -> dict[str, Any]:
    return {
        "status": status,
        "scope": scope,
        "findings": {"contradictions": [], "synthesis_gaps": []},
        "repair_recommendations": [],
        "reviewed_candidate_ids": [],
        "warnings": warnings,
    }


def render_lint_markdown(packet: dict[str, Any]) -> str:
    findings = packet["findings"]
    review = packet.get("semantic_review") or semantic_status("not_run", "one-hop", [])
    lines = [
        "## Wiki Health Report",
        "",
        f"- **Vault:** {packet['vault']}",
        f"- **Issues found:** {packet['summary']['issues_found']}",
        f"- **Semantic review:** {review.get('status', 'unknown')} ({review.get('scope', 'unknown')})",
        "",
    ]
    sections = [
        ("Orphaned Pages", "orphaned_pages"),
        ("Broken Wikilinks", "broken_wikilinks"),
        ("Missing Frontmatter", "missing_frontmatter"),
        ("Missing Summary", "missing_summary"),
        ("Stale Content", "stale_content"),
        ("Index Issues", "index_issues"),
        ("Provenance Issues", "provenance_issues"),
        ("Fragmented Tag Clusters", "fragmented_tag_clusters"),
        ("Visibility Issues", "visibility_issues"),
        ("Misc Promotion Candidates", "misc_promotion_candidates"),
        ("Confidence/Lifecycle Issues", "lifecycle_confidence_issues"),
    ]
    for title, key in sections:
        lines.extend(render_finding_section(title, findings.get(key, [])))
    semantic_findings = review.get("findings") if isinstance(review.get("findings"), dict) else {}
    lines.extend(render_finding_section("Contradictions", semantic_findings.get("contradictions", [])))
    lines.extend(render_finding_section("Synthesis Gaps", semantic_findings.get("synthesis_gaps", [])))
    recommendations = review.get("repair_recommendations") if isinstance(review.get("repair_recommendations"), list) else []
    if recommendations:
        lines.extend(["### Repair Recommendations", ""])
        lines.extend(f"- {format_finding(item)}" for item in recommendations[:20])
        lines.append("")
    if review.get("status") != "completed":
        lines.extend(
            [
                "### Semantic Review Note",
                "",
                "Semantic review was not completed. Raw contradiction and synthesis candidates are available with `a-inf lint --json`.",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def render_finding_section(title: str, items: list[Any]) -> list[str]:
    lines = [f"### {title} ({len(items)} found)", ""]
    if not items:
        lines.extend(["_None._", ""])
        return lines
    lines.extend(f"- {format_finding(item)}" for item in items[:20])
    if len(items) > 20:
        lines.append(f"\n_Showing 20 of {len(items)}._")
    lines.append("")
    return lines


def format_finding(item: Any) -> str:
    if isinstance(item, str):
        return item
    if not isinstance(item, dict):
        return json.dumps(item, sort_keys=True)
    subject = item.get("page") or item.get("tag") or item.get("id") or item.get("pair") or item.get("pages") or "finding"
    message = item.get("message") or item.get("explanation") or item.get("suggested_action") or item.get("evidence") or ""
    extras = []
    for key in ["line", "target", "missing", "cohesion", "age_days", "priority", "cooccurrence", "score"]:
        if key in item:
            extras.append(f"{key}={item[key]}")
    suffix = f" ({', '.join(extras)})" if extras else ""
    return f"`{subject}` — {message}{suffix}" if message else f"`{subject}`{suffix}"


def append_lint_log(vault: Path, packet: dict[str, Any]) -> None:
    log_path = vault / "log.md"
    summary = packet["summary"]["hard_findings"]
    line = (
        f"- [{now_iso()}] LINT issues_found={packet['summary']['issues_found']} "
        f"orphans={summary.get('orphaned_pages', 0)} broken_links={summary.get('broken_wikilinks', 0)} "
        f"stale={summary.get('stale_content', 0)} prov_issues={summary.get('provenance_issues', 0)} "
        f"missing_summary={summary.get('missing_summary', 0)} "
        f"fragmented_clusters={summary.get('fragmented_tag_clusters', 0)} "
        f"visibility_issues={summary.get('visibility_issues', 0)} "
        f"promotion_candidates={summary.get('misc_promotion_candidates', 0)} "
        f"lifecycle_issues={summary.get('lifecycle_confidence_issues', 0)} "
        f"semantic_review={packet.get('semantic_review', {}).get('status', 'unknown')}\n"
    )
    if log_path.exists():
        ensure_managed_tag(log_path, "Wiki Log")
        current = log_path.read_text(encoding="utf-8")
        log_path.write_text(current.rstrip() + "\n" + line, encoding="utf-8")
    else:
        log_path.write_text(f"---\ntitle: Wiki Log\ntags: {managed_tags()}\n---\n\n# Wiki Log\n\n" + line, encoding="utf-8")


def create_lint_run_dir(vault: Path) -> Path:
    run_dir = vault / ".a-inf" / "runs" / f"lint-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    suffix = 1
    candidate = run_dir
    while candidate.exists():
        suffix += 1
        candidate = Path(f"{run_dir}-{suffix}")
    candidate.mkdir(parents=True, exist_ok=True)
    return candidate


def extract_wikilinks(text: str) -> list[dict[str, Any]]:
    links = []
    pattern = re.compile(r"\[\[([^\]]+)\]\]")
    for line_no, line in enumerate(text.splitlines(), start=1):
        for match in pattern.finditer(line):
            raw_target = match.group(1)
            target = raw_target.split("|", 1)[0].strip()
            links.append({"raw": match.group(0), "target": target, "line": line_no})
    return links


def clean_link_target(value: str) -> str:
    target = value.split("|", 1)[0].split("#", 1)[0].strip()
    target = target.strip("/").replace("\\", "/")
    if target.startswith("./"):
        target = target[2:]
    return target


def strip_frontmatter(text: str) -> str:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return text
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return "\n".join(lines[index + 1 :])
    return text


def read_frontmatter_sequence(path: Path, key: str) -> list[str]:
    lines = read_text(path).splitlines()
    if not lines or lines[0].strip() != "---":
        return []
    values: list[str] = []
    in_key = False
    prefix = f"{key}:"
    for line in lines[1:]:
        stripped = line.strip()
        if stripped == "---":
            break
        if stripped.startswith(prefix):
            in_key = True
            inline = stripped[len(prefix) :].strip()
            if inline.startswith("[") and inline.endswith("]"):
                return parse_inline_list(inline)
            if inline:
                values.append(inline.strip('"').strip("'"))
            continue
        if in_key and (line.startswith(" ") or line.startswith("-")):
            value = stripped.lstrip("-").strip().strip('"').strip("'")
            if value:
                values.append(value)
            continue
        if in_key:
            break
    return values


def parse_inline_list(value: str) -> list[str]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        parsed = [part.strip().strip('"').strip("'") for part in value.strip("[]").split(",")]
    return [str(item) for item in parsed if str(item)] if isinstance(parsed, list) else []


def as_text_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def count_provenance(body: str) -> dict[str, float]:
    units = [line for line in body.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    total = max(len(units), 1)
    inferred = sum(1 for line in units if "^[inferred]" in line)
    ambiguous = sum(1 for line in units if "^[ambiguous]" in line)
    extracted = max(total - inferred - ambiguous, 0)
    return {
        "extracted": round(extracted / total, 3),
        "inferred": round(inferred / total, 3),
        "ambiguous": round(ambiguous / total, 3),
    }


def project_for_page(rel: str, page: Page | None) -> str | None:
    parts = rel.split("/")
    if len(parts) >= 2 and parts[0] == "projects":
        return parts[1]
    if page and page.frontmatter.get("project"):
        return str(page.frontmatter["project"])
    return None


def normalize_slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
