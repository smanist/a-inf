from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from a_inf import lint
from a_inf.ingest import load_wiki_config, parse_frontmatter_file, render_frontmatter, write_json
from a_inf.managed_files import ensure_managed_tag, managed_tags
from a_inf.qmd import ensure_qmd_collection, qmd_env, sync_qmd


MAX_CANDIDATES = 200
MAX_RELATED_PER_CLUSTER = 80
VALID_ACTIONS = {"add_inline", "add_related", "skip"}


class FixlinkError(Exception):
    pass


@dataclass(frozen=True)
class Run:
    run_dir: Path
    packet_path: Path
    repair_plan_path: Path
    report_path: Path


@dataclass(frozen=True)
class ValidatedDecision:
    candidate: dict[str, Any]
    action: str
    match_id: str | None = None
    note: str = ""


@dataclass(frozen=True)
class ValidatedRepairPlan:
    raw: dict[str, Any]
    decisions: list[ValidatedDecision]
    warnings: list[str]


def run_fixlink(args: Any, vault: Path, config: dict[str, str] | None = None) -> int:
    config = config or load_wiki_config(vault)
    run = create_run(vault)
    lint_packet = lint.build_lint_packet(vault, config)
    packet = build_fixlink_packet(vault, config, lint_packet, run.repair_plan_path)
    write_json(run.packet_path, packet)

    if getattr(args, "print_prompt", False):
        print(build_prompt(vault, run.packet_path, run.repair_plan_path))
        return 0

    if getattr(args, "no_codex", False):
        report = build_report(packet, status="not_run", warnings=["repair planning skipped by --no-codex"])
        write_json(run.report_path, report)
        print_report(report, json_output=getattr(args, "json", False))
        return 0

    codex_bin = shutil.which(getattr(args, "codex_bin", "codex"))
    if codex_bin is None:
        print("Codex executable not found. Re-run with --print-prompt or install Codex CLI.", file=sys.stderr)
        print(build_prompt(vault, run.packet_path, run.repair_plan_path))
        return 127

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
    command.append(build_prompt(vault, run.packet_path, run.repair_plan_path))
    result = subprocess.call(command, cwd=vault, env=qmd_env(os.environ, vault))
    if result != 0:
        return result

    try:
        repair_plan = read_repair_plan(run.repair_plan_path)
        validated = validate_repair_plan(repair_plan, packet, vault)
        if getattr(args, "dry_run", False):
            report = build_report(packet, status="dry_run", validated=validated)
        else:
            applied = apply_repair_plan(validated, vault, config)
            post_packet = lint.build_lint_packet(vault, config)
            if (applied["links_added"] or applied["misc_affinity_updated"]) and not getattr(args, "no_log", False):
                append_log(vault, applied, post_packet)
                write_hot(vault, applied)
            qmd_warning: list[str] = []
            if (applied["links_added"] or applied["misc_affinity_updated"]) and getattr(args, "sandbox", "workspace-write") != "read-only":
                if ensure_qmd_collection(vault, config) and not sync_qmd(vault, config):
                    qmd_warning.append("QMD sync failed after fixlink; wiki files were still updated.")
            report = build_report(
                packet,
                status="completed",
                validated=validated,
                applied=applied,
                post_lint_packet=post_packet,
                warnings=[*validated.warnings, *qmd_warning],
            )
        write_json(run.report_path, report)
        print_report(report, json_output=getattr(args, "json", False))
        return 0
    except FixlinkError as exc:
        report = build_report(packet, status="invalid", warnings=[str(exc)])
        write_json(run.report_path, report)
        print_report(report, json_output=getattr(args, "json", False))
        return 1


def build_fixlink_packet(
    vault: Path, config: dict[str, str], lint_packet: dict[str, Any], repair_plan_path: Path
) -> dict[str, Any]:
    pages = lint.build_page_registry(vault)
    links = lint.build_link_graph(pages)
    candidates = build_candidates(pages, links, lint_packet)
    return {
        "version": 1,
        "generated_at": now_iso(),
        "vault": str(vault),
        "link_format": config.get("OBSIDIAN_LINK_FORMAT", "wikilink"),
        "repair_plan_path": str(repair_plan_path),
        "lint_packet": lint_packet,
        "candidates": candidates,
        "summary": {
            "candidates": len(candidates),
            "inline_candidates": sum(1 for item in candidates if item["kind"] == "inline"),
            "related_candidates": sum(1 for item in candidates if item["kind"] == "related"),
            "orphan_targets": len(lint_packet.get("findings", {}).get("orphaned_pages", [])),
            "fragmented_tag_clusters": len(lint_packet.get("findings", {}).get("fragmented_tag_clusters", [])),
        },
    }


def build_candidates(
    pages: dict[str, lint.Page], links: dict[str, Any], lint_packet: dict[str, Any]
) -> list[dict[str, Any]]:
    existing = existing_pairs(links)
    candidates: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    target_reasons = target_pages_from_findings(lint_packet)

    for source in sorted(pages.values(), key=lambda page: page.rel):
        for target_rel, reasons in sorted(target_reasons.items()):
            if source.rel == target_rel or target_rel not in pages or (source.rel, target_rel) in existing:
                continue
            matches = find_inline_matches(source, pages[target_rel])
            if not matches:
                continue
            key = ("inline", source.rel, target_rel)
            if key in seen:
                continue
            seen.add(key)
            candidates.append(
                make_candidate(
                    "inline",
                    source.rel,
                    target_rel,
                    pages,
                    links,
                    sorted(reasons | {"exact_mention"}),
                    matches=matches,
                )
            )

    for candidate in related_candidates_from_clusters(pages, links, lint_packet, existing):
        key = ("related", candidate["source"], candidate["target"])
        if key in seen:
            continue
        seen.add(key)
        candidates.append(candidate)

    for candidate in related_candidates_from_misc(pages, links, lint_packet, existing):
        key = ("related", candidate["source"], candidate["target"])
        if key in seen:
            continue
        seen.add(key)
        candidates.append(candidate)

    for index, candidate in enumerate(candidates[:MAX_CANDIDATES], start=1):
        candidate["candidate_id"] = f"fixlink-{index:04d}"
    return candidates[:MAX_CANDIDATES]


def target_pages_from_findings(lint_packet: dict[str, Any]) -> dict[str, set[str]]:
    targets: dict[str, set[str]] = defaultdict(set)
    findings = lint_packet.get("findings", {})
    if isinstance(findings, dict):
        for item in findings.get("orphaned_pages", []) or []:
            if isinstance(item, dict) and item.get("page"):
                targets[str(item["page"])].add("orphan")
        for cluster in findings.get("fragmented_tag_clusters", []) or []:
            if not isinstance(cluster, dict):
                continue
            tag = str(cluster.get("tag") or "")
            for page, meta in lint_packet.get("page_registry", {}).items():
                if isinstance(meta, dict) and tag in [str(value) for value in meta.get("tags", [])]:
                    targets[str(page)].add(f"fragmented_tag:{tag}")
    return targets


def related_candidates_from_clusters(
    pages: dict[str, lint.Page],
    links: dict[str, Any],
    lint_packet: dict[str, Any],
    existing: set[tuple[str, str]],
) -> Iterable[dict[str, Any]]:
    findings = lint_packet.get("findings", {})
    clusters = findings.get("fragmented_tag_clusters", []) if isinstance(findings, dict) else []
    for cluster in clusters:
        if not isinstance(cluster, dict):
            continue
        tag = str(cluster.get("tag") or "")
        cluster_pages = sorted(page.rel for page in pages.values() if tag in page.tags)
        emitted = 0
        for source in cluster_pages:
            for target in cluster_pages:
                if source == target or (source, target) in existing:
                    continue
                if emitted >= MAX_RELATED_PER_CLUSTER:
                    break
                emitted += 1
                yield make_candidate(
                    "related",
                    source,
                    target,
                    pages,
                    links,
                    [f"fragmented_tag:{tag}"],
                    note_hint=f"Shares low-cohesion tag #{tag}.",
                )
            if emitted >= MAX_RELATED_PER_CLUSTER:
                break


def related_candidates_from_misc(
    pages: dict[str, lint.Page],
    links: dict[str, Any],
    lint_packet: dict[str, Any],
    existing: set[tuple[str, str]],
) -> Iterable[dict[str, Any]]:
    findings = lint_packet.get("findings", {})
    misc_items = findings.get("misc_promotion_candidates", []) if isinstance(findings, dict) else []
    for item in misc_items:
        if not isinstance(item, dict):
            continue
        misc_page = str(item.get("page") or "")
        project = str(item.get("top_project") or "")
        project_page = f"projects/{project}.md"
        if project_page not in pages:
            project_page = f"projects/{project}/{project}.md"
        if misc_page not in pages or project_page not in pages or (misc_page, project_page) in existing:
            continue
        yield make_candidate(
            "related",
            misc_page,
            project_page,
            pages,
            links,
            ["misc_promotion_candidate"],
            note_hint=f"Top project affinity score {item.get('affinity_score')}.",
        )


def make_candidate(
    kind: str,
    source: str,
    target: str,
    pages: dict[str, lint.Page],
    links: dict[str, Any],
    reasons: list[str],
    *,
    matches: list[dict[str, Any]] | None = None,
    note_hint: str = "",
) -> dict[str, Any]:
    incoming = links["incoming_counts"]
    outgoing = links["outgoing_counts"]
    candidate = {
        "candidate_id": "",
        "kind": kind,
        "source": source,
        "target": target,
        "source_title": pages[source].title,
        "target_title": pages[target].title,
        "source_tags": pages[source].tags,
        "target_tags": pages[target].tags,
        "reasons": reasons,
        "score": score_candidate(source, target, pages, links, reasons, bool(matches)),
        "source_outgoing": outgoing.get(source, 0),
        "target_incoming": incoming.get(target, 0),
    }
    if matches is not None:
        candidate["matches"] = matches
    if note_hint:
        candidate["note_hint"] = note_hint
    return candidate


def score_candidate(
    source: str,
    target: str,
    pages: dict[str, lint.Page],
    links: dict[str, Any],
    reasons: list[str],
    has_match: bool,
) -> int:
    score = 4 if has_match else 0
    shared_tags = set(pages[source].tags).intersection(pages[target].tags)
    shared_tags = {tag for tag in shared_tags if not tag.startswith("visibility/")}
    if len(shared_tags) >= 2:
        score += 2
    if any(reason.startswith("fragmented_tag:") for reason in reasons):
        score += 2
    if source.split("/", 2)[:2] == target.split("/", 2)[:2] and source.startswith("projects/"):
        score += 2
    if pages[source].category != pages[target].category:
        score += 2
    if links["outgoing_counts"].get(source, 0) <= 2 and links["incoming_counts"].get(target, 0) >= 8:
        score += 2
    if "orphan" in reasons:
        score += 2
    return score


def existing_pairs(links: dict[str, Any]) -> set[tuple[str, str]]:
    return {(edge["source"], edge["resolved"]) for edge in links["resolved_edges"]}


def find_inline_matches(source: lint.Page, target: lint.Page) -> list[dict[str, Any]]:
    terms = distinctive_terms(target)
    if not terms:
        return []
    text = source.path.read_text(encoding="utf-8")
    ranges = editable_ranges(text)
    matches: list[dict[str, Any]] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        occupied: list[tuple[int, int]] = []
        for term in terms:
            pattern = re.compile(rf"(?<![\w/])({re.escape(term)})(?![\w/-])", re.IGNORECASE)
            for match in pattern.finditer(line):
                if any(match.start() < end and match.end() > start for start, end in occupied):
                    continue
                if not offset_is_editable(text, line_no, match.start(), ranges):
                    continue
                if inside_existing_wikilink(line, match.start()):
                    continue
                occupied.append((match.start(), match.end()))
                matches.append(
                    {
                        "match_id": f"m{len(matches) + 1:03d}",
                        "line": line_no,
                        "start": match.start(),
                        "end": match.end(),
                        "text": match.group(0),
                    }
                )
                if len(matches) >= 5:
                    return matches
    return matches


def distinctive_terms(page: lint.Page) -> list[str]:
    raw_terms = [page.title, page.stem.replace("-", " "), page.stem, *page.aliases]
    terms = []
    seen = set()
    for term in raw_terms:
        normalized = " ".join(str(term).split()).strip()
        if len(normalized) < 4 or normalized.lower() in {"this", "that", "with", "from", "page"}:
            continue
        key = normalized.lower()
        if key not in seen:
            seen.add(key)
            terms.append(normalized)
    return sorted(terms, key=lambda value: (-len(value), value.lower()))


def editable_ranges(text: str) -> set[int]:
    editable: set[int] = set()
    in_frontmatter = False
    in_code = False
    for line_no, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if line_no == 1 and stripped == "---":
            in_frontmatter = True
            continue
        if in_frontmatter:
            if stripped == "---":
                in_frontmatter = False
            continue
        if stripped.startswith("```"):
            in_code = not in_code
            continue
        if not in_code:
            editable.add(line_no)
    return editable


def offset_is_editable(_text: str, line_no: int, _start: int, editable: set[int]) -> bool:
    return line_no in editable


def inside_existing_wikilink(line: str, start: int) -> bool:
    before = line[:start]
    open_pos = before.rfind("[[")
    close_pos = before.rfind("]]")
    return open_pos > close_pos


def build_prompt(vault: Path, packet_path: Path, repair_plan_path: Path) -> str:
    return (
        "Use the `wiki-fixlink` skill to review this deterministic fixlink packet.\n\n"
        f"Vault path: {vault}\n"
        f"Deterministic packet path: {packet_path}\n"
        f"Write repair plan JSON to: {repair_plan_path}\n\n"
        "Read the packet first. Do not edit wiki files. Write exactly one JSON object with this shape:\n"
        "{\n"
        '  "version": 1,\n'
        '  "status": "completed",\n'
        '  "decisions": [],\n'
        '  "warnings": []\n'
        "}\n"
        "Each decision must include candidate_id and action. action must be add_inline, add_related, or skip. "
        "For add_inline, include match_id from the packet. For add_related, include a short note."
    )


def read_repair_plan(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FixlinkError(f"could not read repair_plan.json: {exc}") from exc
    if not isinstance(data, dict):
        raise FixlinkError("repair_plan must be a JSON object")
    return data


def validate_repair_plan(plan: dict[str, Any], packet: dict[str, Any], vault: Path) -> ValidatedRepairPlan:
    if plan.get("version") != 1:
        raise FixlinkError("repair_plan version must be 1")
    if plan.get("status") != "completed":
        raise FixlinkError("repair_plan status must be completed")
    raw_decisions = plan.get("decisions")
    if not isinstance(raw_decisions, list):
        raise FixlinkError("repair_plan decisions must be a list")
    warnings = [str(item) for item in plan.get("warnings", [])] if isinstance(plan.get("warnings", []), list) else []
    candidates = {candidate["candidate_id"]: candidate for candidate in packet.get("candidates", [])}
    seen: set[str] = set()
    validated: list[ValidatedDecision] = []
    current_pages = lint.build_page_registry(vault)
    current_links = lint.build_link_graph(current_pages)
    existing = existing_pairs(current_links)
    resolver = lint.LinkResolver(current_pages)

    for decision in raw_decisions:
        if not isinstance(decision, dict):
            raise FixlinkError("each decision must be an object")
        candidate_id = str(decision.get("candidate_id") or "")
        if candidate_id in seen:
            raise FixlinkError(f"duplicate decision for {candidate_id}")
        seen.add(candidate_id)
        candidate = candidates.get(candidate_id)
        if candidate is None:
            raise FixlinkError(f"unknown candidate_id: {candidate_id}")
        action = str(decision.get("action") or "")
        if action not in VALID_ACTIONS:
            raise FixlinkError(f"invalid action for {candidate_id}: {action}")
        source = str(candidate["source"])
        target = str(candidate["target"])
        if source not in current_pages or target not in current_pages:
            raise FixlinkError(f"candidate source/target missing for {candidate_id}")
        if resolver.resolve(target) != target:
            raise FixlinkError(f"candidate target does not resolve for {candidate_id}: {target}")
        if (source, target) in existing and action != "skip":
            raise FixlinkError(f"candidate already linked for {candidate_id}")
        if action == "add_inline":
            if candidate.get("kind") != "inline":
                raise FixlinkError(f"add_inline requires inline candidate: {candidate_id}")
            match_id = str(decision.get("match_id") or "")
            match = match_by_id(candidate, match_id)
            if match is None:
                raise FixlinkError(f"unknown match_id for {candidate_id}: {match_id}")
            assert_match_still_valid(vault / source, match)
            validated.append(ValidatedDecision(candidate=candidate, action=action, match_id=match_id))
        elif action == "add_related":
            if candidate.get("kind") not in {"inline", "related"}:
                raise FixlinkError(f"add_related requires link candidate: {candidate_id}")
            note = str(decision.get("note") or candidate.get("note_hint") or f"Related to {candidate.get('target_title')}.")
            validated.append(ValidatedDecision(candidate=candidate, action=action, note=note.strip()))
        else:
            validated.append(ValidatedDecision(candidate=candidate, action=action))
    return ValidatedRepairPlan(raw=plan, decisions=validated, warnings=warnings)


def match_by_id(candidate: dict[str, Any], match_id: str) -> dict[str, Any] | None:
    for match in candidate.get("matches", []) or []:
        if isinstance(match, dict) and match.get("match_id") == match_id:
            return match
    return None


def assert_match_still_valid(path: Path, match: dict[str, Any]) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    line_no = int(match["line"])
    if line_no < 1 or line_no > len(lines):
        raise FixlinkError(f"inline match line no longer exists in {path}")
    line = lines[line_no - 1]
    start = int(match["start"])
    end = int(match["end"])
    if line[start:end] != str(match["text"]):
        raise FixlinkError(f"inline match text changed in {path}")
    if inside_existing_wikilink(line, start):
        raise FixlinkError(f"inline match is already inside a wikilink in {path}")


def apply_repair_plan(plan: ValidatedRepairPlan, vault: Path, config: dict[str, str]) -> dict[str, Any]:
    by_source: dict[str, list[ValidatedDecision]] = defaultdict(list)
    for decision in plan.decisions:
        if decision.action != "skip":
            by_source[str(decision.candidate["source"])].append(decision)

    modified_pages: set[str] = set()
    links_added = 0
    related_added = 0
    inline_added = 0
    for source, decisions in sorted(by_source.items()):
        path = vault / source
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()
        source_modified = False
        inline_decisions = [decision for decision in decisions if decision.action == "add_inline"]
        for decision in sorted(inline_decisions, key=lambda item: inline_sort_key(item), reverse=True):
            match = match_by_id(decision.candidate, decision.match_id or "")
            if match is None:
                continue
            line_index = int(match["line"]) - 1
            start = int(match["start"])
            end = int(match["end"])
            display = lines[line_index][start:end]
            link = format_link(Path(source), Path(str(decision.candidate["target"])), display, config)
            lines[line_index] = lines[line_index][:start] + link + lines[line_index][end:]
            inline_added += 1
            links_added += 1
            source_modified = True
        text = "\n".join(lines) + ("\n" if text.endswith("\n") else "")
        related_decisions = [decision for decision in decisions if decision.action == "add_related"]
        if related_decisions:
            text, count = append_related_links(text, source, related_decisions, config)
            related_added += count
            links_added += count
            source_modified = source_modified or count > 0
        if source_modified:
            path.write_text(text, encoding="utf-8")
            modified_pages.add(source)

    affinity_updates = update_misc_affinity(vault)
    modified_pages.update(affinity_updates)
    return {
        "links_added": links_added,
        "inline_added": inline_added,
        "related_added": related_added,
        "pages_modified": sorted(modified_pages),
        "misc_affinity_updated": len(affinity_updates),
        "decisions_applied": len([decision for decision in plan.decisions if decision.action != "skip"]),
    }


def inline_sort_key(decision: ValidatedDecision) -> tuple[int, int]:
    match = match_by_id(decision.candidate, decision.match_id or "") or {}
    return int(match.get("line", 0)), int(match.get("start", 0))


def append_related_links(
    text: str, source: str, decisions: list[ValidatedDecision], config: dict[str, str]
) -> tuple[str, int]:
    lines = text.rstrip().splitlines()
    try:
        heading_index = next(index for index, line in enumerate(lines) if line.strip() == "## Related")
    except StopIteration:
        lines.extend(["", "## Related", ""])
        heading_index = len(lines) - 2
    insert_at = len(lines)
    for index in range(heading_index + 1, len(lines)):
        if lines[index].startswith("## ") and index != heading_index:
            insert_at = index
            break
    existing = "\n".join(lines[heading_index:insert_at])
    additions = []
    for decision in decisions:
        target = Path(str(decision.candidate["target"]))
        link = format_link(Path(source), target, str(decision.candidate["target_title"]), config)
        if link in existing:
            continue
        note = decision.note.rstrip(".")
        additions.append(f"- {link} - {note}.")
    if not additions:
        return text, 0
    lines[insert_at:insert_at] = additions + ([] if insert_at == len(lines) else [""])
    return "\n".join(lines).rstrip() + "\n", len(additions)


def format_link(current: Path, target: Path, display: str, config: dict[str, str]) -> str:
    if config.get("OBSIDIAN_LINK_FORMAT", "wikilink") == "markdown":
        rel = os.path.relpath(target, start=current.parent)
        return f"[{display}]({rel})"
    no_ext = target.with_suffix("").as_posix()
    natural = display.strip()
    if natural == Path(no_ext).name or natural == no_ext:
        return f"[[{no_ext}]]"
    return f"[[{no_ext}|{natural}]]"


def update_misc_affinity(vault: Path) -> set[str]:
    pages = lint.build_page_registry(vault)
    links = lint.build_link_graph(pages)
    neighbors: dict[str, set[str]] = defaultdict(set)
    for edge in links["resolved_edges"]:
        neighbors[edge["source"]].add(edge["resolved"])
        neighbors[edge["resolved"]].add(edge["source"])
    updated: set[str] = set()
    for page in pages.values():
        if not (page.rel.startswith("misc/") or page.frontmatter.get("promotion_status") == "misc"):
            continue
        scores: dict[str, int] = defaultdict(int)
        for neighbor in neighbors.get(page.rel, set()):
            project = lint.project_for_page(neighbor, pages.get(neighbor))
            if project:
                scores[project] += 1
        if not scores:
            continue
        if rewrite_affinity(vault / page.rel, dict(sorted(scores.items()))):
            updated.add(page.rel)
    return updated


def rewrite_affinity(path: Path, affinity: dict[str, int]) -> bool:
    text = path.read_text(encoding="utf-8")
    fm = parse_frontmatter_file(path)
    if fm.get("affinity") == affinity:
        return False
    fm["affinity"] = affinity
    body = lint.strip_frontmatter(text)
    path.write_text("---\n" + render_frontmatter(fm) + "---\n\n" + body.strip() + "\n", encoding="utf-8")
    return True


def append_log(vault: Path, applied: dict[str, Any], post_packet: dict[str, Any]) -> None:
    line = (
        f"- [{now_iso()}] FIXLINK links_added={applied['links_added']} "
        f"pages_modified={len(applied['pages_modified'])} "
        f"orphans_remaining={len(post_packet.get('findings', {}).get('orphaned_pages', []))} "
        f"fragmented_clusters_remaining={len(post_packet.get('findings', {}).get('fragmented_tag_clusters', []))} "
        f"misc_affinity_updated={applied['misc_affinity_updated']}\n"
    )
    path = vault / "log.md"
    if path.exists():
        ensure_managed_tag(path, "Wiki Log")
        path.write_text(path.read_text(encoding="utf-8").rstrip() + "\n" + line, encoding="utf-8")
    else:
        path.write_text(f"---\ntitle: Wiki Log\ntags: {managed_tags()}\n---\n\n# Wiki Log\n\n" + line, encoding="utf-8")


def write_hot(vault: Path, applied: dict[str, Any]) -> None:
    now = now_iso()
    current = vault / "hot.md"
    recent_line = (
        f"Fixlinked {applied['links_added']} links across {len(applied['pages_modified'])} pages; "
        f"{applied['misc_affinity_updated']} misc affinity blocks updated."
    )
    content = [
        "---",
        "title: Hot Cache",
        f"tags: {managed_tags()}",
        f"updated: {now}",
        "---",
        "",
        "# Hot Cache",
        "",
        "## Recent Activity",
        f"- {recent_line}",
        "",
        "## Active Threads",
        "- None.",
        "",
        "## Key Takeaways",
        "- None.",
        "",
        "## Flagged Contradictions",
        "- None.",
        "",
    ]
    if current.exists():
        old = current.read_text(encoding="utf-8")
        activity = extract_recent_activity(old)
        content[8:9] = [f"- {recent_line}", *activity[:2]]
    current.write_text("\n".join(content), encoding="utf-8")


def extract_recent_activity(text: str) -> list[str]:
    lines = text.splitlines()
    try:
        start = lines.index("## Recent Activity") + 1
    except ValueError:
        return []
    result = []
    for line in lines[start:]:
        if line.startswith("## "):
            break
        if line.startswith("- "):
            result.append(line)
    return result


def build_report(
    packet: dict[str, Any],
    *,
    status: str,
    validated: ValidatedRepairPlan | None = None,
    applied: dict[str, Any] | None = None,
    post_lint_packet: dict[str, Any] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    pre = packet["lint_packet"]
    post = post_lint_packet or pre
    return {
        "version": 1,
        "status": status,
        "summary": {
            "candidates": len(packet.get("candidates", [])),
            "decisions": len(validated.decisions) if validated else 0,
            "links_added": (applied or {}).get("links_added", 0),
            "pages_modified": len((applied or {}).get("pages_modified", [])),
            "orphans_before": len(pre.get("findings", {}).get("orphaned_pages", [])),
            "orphans_after": len(post.get("findings", {}).get("orphaned_pages", [])),
            "fragmented_clusters_before": len(pre.get("findings", {}).get("fragmented_tag_clusters", [])),
            "fragmented_clusters_after": len(post.get("findings", {}).get("fragmented_tag_clusters", [])),
        },
        "applied": applied or {
            "links_added": 0,
            "inline_added": 0,
            "related_added": 0,
            "pages_modified": [],
            "misc_affinity_updated": 0,
            "decisions_applied": 0,
        },
        "warnings": warnings or [],
        "candidate_sample": packet.get("candidates", [])[:20],
    }


def print_report(report: dict[str, Any], *, json_output: bool) -> None:
    if json_output:
        print(json.dumps(report, indent=2, sort_keys=True))
        return
    summary = report["summary"]
    lines = [
        "## Wiki Fixlink Report",
        "",
        f"- **Status:** {report['status']}",
        f"- **Candidates:** {summary['candidates']}",
        f"- **Decisions:** {summary['decisions']}",
        f"- **Links added:** {summary['links_added']}",
        f"- **Pages modified:** {summary['pages_modified']}",
        f"- **Orphans:** {summary['orphans_before']} -> {summary['orphans_after']}",
        f"- **Fragmented tag clusters:** {summary['fragmented_clusters_before']} -> {summary['fragmented_clusters_after']}",
    ]
    if report.get("warnings"):
        lines.extend(["", "### Warnings", ""])
        lines.extend(f"- {warning}" for warning in report["warnings"])
    print("\n".join(lines).rstrip() + "\n")


def create_run(vault: Path) -> Run:
    base = vault / ".a-inf" / "runs" / f"fixlink-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    candidate = base
    suffix = 1
    while candidate.exists():
        suffix += 1
        candidate = Path(f"{base}-{suffix}")
    candidate.mkdir(parents=True, exist_ok=True)
    return Run(
        run_dir=candidate,
        packet_path=candidate / "packet.json",
        repair_plan_path=candidate / "repair_plan.json",
        report_path=candidate / "report.json",
    )


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
