from __future__ import annotations

from collections import defaultdict
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

from a_inf import lint
from a_inf.ingest import (
    as_text_list,
    count_wiki_pages,
    load_wiki_config,
    parse_datetime,
    read_manifest,
    read_optional,
    rebuild_index,
    render_page,
    write_json,
)
from a_inf.managed_files import ensure_managed_tag, managed_tags
from a_inf.qmd import ensure_qmd_collection, qmd_env, sync_qmd
from a_inf.runs import runs_root, timestamped_run_dir


MAX_AUTHORING_CANDIDATES = 5
MAX_OPPORTUNITIES = 10
MAX_EXCERPT_CHARS = 1600
VALID_ACTIONS = {"create", "skip"}


class SynthesizeError(Exception):
    pass


@dataclass(frozen=True)
class Run:
    run_dir: Path
    packet_path: Path
    synthesis_plan_path: Path
    report_path: Path


@dataclass(frozen=True)
class ValidatedDecision:
    candidate: dict[str, Any]
    action: str
    summary: str = ""
    body: str = ""
    open_questions: list[str] | None = None
    note: str = ""


@dataclass(frozen=True)
class ValidatedSynthesisPlan:
    raw: dict[str, Any]
    decisions: list[ValidatedDecision]
    warnings: list[str]


def run_synthesize(args: Any, vault: Path, config: dict[str, str] | None = None) -> int:
    config = config or load_wiki_config(vault)
    run = create_run(vault)
    lint_packet, semantic_review, lint_source = load_candidate_context(vault, config)
    packet = build_synthesis_packet(
        vault,
        config,
        lint_packet,
        semantic_review,
        run.synthesis_plan_path,
        list(getattr(args, "args", [])),
        lint_source,
    )
    write_json(run.packet_path, packet)

    if getattr(args, "print_prompt", False):
        print(build_prompt(vault, run.packet_path, run.synthesis_plan_path))
        return 0

    if getattr(args, "no_codex", False):
        report = build_report(packet, status="not_run", warnings=["synthesis planning skipped by --no-codex"])
        write_json(run.report_path, report)
        print_report(report, json_output=getattr(args, "json", False))
        open_synthesize_output_if_requested(args, vault, run, report)
        return 0

    codex_bin = shutil.which(getattr(args, "codex_bin", "codex"))
    if codex_bin is None:
        print("Codex executable not found. Re-run with --print-prompt or install Codex CLI.", file=sys.stderr)
        print(build_prompt(vault, run.packet_path, run.synthesis_plan_path))
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
    command.append(build_prompt(vault, run.packet_path, run.synthesis_plan_path))
    result = subprocess.call(command, cwd=vault, env=qmd_env(os.environ, vault))
    if result != 0:
        return result

    try:
        raw_plan = read_synthesis_plan(run.synthesis_plan_path)
        validated = validate_synthesis_plan(raw_plan, packet, vault)
        if getattr(args, "dry_run", False):
            report = build_report(packet, status="dry_run", validated=validated)
        else:
            applied = apply_synthesis_plan(validated, vault, config)
            if applied["pages_created"] and not getattr(args, "no_log", False):
                append_log(vault, packet, applied)
            if applied["pages_created"]:
                write_hot(vault, validated, applied)
                update_manifest_stats(vault)
            qmd_warnings: list[str] = []
            if applied["pages_created"] and getattr(args, "sandbox", "workspace-write") != "read-only":
                if ensure_qmd_collection(vault, config) and not sync_qmd(vault, config):
                    qmd_warnings.append("QMD sync failed after synthesize; wiki files were still updated.")
            report = build_report(
                packet,
                status="completed",
                validated=validated,
                applied=applied,
                warnings=[*validated.warnings, *qmd_warnings],
            )
        write_json(run.report_path, report)
        print_report(report, json_output=getattr(args, "json", False))
        open_synthesize_output_if_requested(args, vault, run, report)
        return 0
    except SynthesizeError as exc:
        report = build_report(packet, status="invalid", warnings=[str(exc)])
        write_json(run.report_path, report)
        print_report(report, json_output=getattr(args, "json", False))
        open_synthesize_output_if_requested(args, vault, run, report)
        return 1


def open_synthesize_output_if_requested(args: Any, vault: Path, run: Run, report: dict[str, Any]) -> None:
    if not getattr(args, "vscode", False):
        return
    created = [vault / str(path) for path in report.get("pages_created", []) if str(path)]
    paths = [path for path in created if path.exists()] or [run.report_path]
    open_paths_in_vscode(getattr(args, "vscode_bin", "code"), paths)


def open_paths_in_vscode(vscode_bin: str, paths: list[Path]) -> None:
    binary = shutil.which(vscode_bin)
    if binary is None:
        joined = ", ".join(str(path) for path in paths)
        print(f"warning: VS Code executable not found, could not open {joined}", file=sys.stderr)
        return
    result = subprocess.run([binary, *[str(path) for path in paths]])
    if result.returncode != 0:
        joined = ", ".join(str(path) for path in paths)
        print(f"warning: VS Code exited with status {result.returncode} while opening {joined}", file=sys.stderr)


def load_candidate_context(vault: Path, config: dict[str, str]) -> tuple[dict[str, Any], dict[str, Any], str]:
    bundle = newest_fresh_lint_bundle(vault)
    if bundle is not None:
        packet_path, review_path = bundle
        packet = read_json_object(packet_path)
        review = read_json_object(review_path)
        if review.get("status") == "completed":
            return packet, review, str(packet_path.parent)
    return lint.build_lint_packet(vault, config), lint.semantic_status("not_run", "one-hop", []), "inline_lint"


def newest_fresh_lint_bundle(vault: Path) -> tuple[Path, Path] | None:
    runs = runs_root(vault)
    if not runs.exists():
        return None
    for run_dir in sorted(runs.glob("lint-*"), key=lambda path: path.name, reverse=True):
        packet_path = run_dir / "packet.json"
        review_path = run_dir / "semantic_review.json"
        if not packet_path.exists() or not review_path.exists():
            continue
        try:
            packet = read_json_object(packet_path)
            review = read_json_object(review_path)
        except SynthesizeError:
            continue
        if review.get("status") != "completed":
            continue
        generated = parse_datetime(str(packet.get("generated_at") or ""))
        if generated is None or content_newer_than(vault, generated):
            continue
        return packet_path, review_path
    return None


def content_newer_than(vault: Path, timestamp: datetime) -> bool:
    for rel in lint.iter_content_markdown(vault):
        path = vault / rel
        modified = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
        if modified > timestamp:
            return True
    return False


def read_json_object(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SynthesizeError(f"could not read JSON object at {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise SynthesizeError(f"JSON at {path} must be an object")
    return data


def build_synthesis_packet(
    vault: Path,
    config: dict[str, str],
    lint_packet: dict[str, Any],
    semantic_review: dict[str, Any],
    synthesis_plan_path: Path,
    workflow_args: list[str],
    lint_source: str = "inline_lint",
) -> dict[str, Any]:
    pages = lint.build_page_registry(vault)
    links = lint.build_link_graph(vault, pages)
    candidates = canonical_candidates(lint_packet, semantic_review, pages, links)
    candidates = filter_candidates(candidates, pages, workflow_args)
    selected = candidates[:MAX_AUTHORING_CANDIDATES]
    opportunities = candidates[MAX_AUTHORING_CANDIDATES : MAX_AUTHORING_CANDIDATES + MAX_OPPORTUNITIES]
    page_registry = {
        rel: {
            "title": page.title,
            "category": page.category,
            "tags": page.tags,
            "summary": page.summary,
            "updated": page.updated,
            "lifecycle": page.lifecycle,
            "base_confidence": page.base_confidence,
            "aliases": page.aliases,
        }
        for rel, page in pages.items()
    }
    return {
        "version": 1,
        "generated_at": now_iso(),
        "vault": str(vault),
        "link_format": config.get("OBSIDIAN_LINK_FORMAT", "wikilink"),
        "synthesis_plan_path": str(synthesis_plan_path),
        "lint_source": lint_source,
        "topic_filter": " ".join(workflow_args).strip(),
        "summary": {
            "pages_scanned": len(pages),
            "candidates": len(candidates),
            "selected": len(selected),
            "opportunities": len(opportunities),
        },
        "candidates": selected,
        "opportunities": opportunities,
        "page_registry": page_registry,
        "graph": {
            "incoming_counts": links["incoming_counts"],
            "outgoing_counts": links["outgoing_counts"],
        },
        "existing_synthesis_pages": existing_synthesis_pages(pages, links),
        "authoring_context": authoring_context(pages, selected),
        "index_summary": trim(read_optional(vault / "index.md"), 3000),
        "hot": trim(read_optional(vault / "hot.md"), 2200),
        "taxonomy": trim(read_optional(vault / "_meta" / "taxonomy.md"), 2200),
        "agents": trim(read_optional(vault / "AGENTS.md"), 2200),
    }


def canonical_candidates(
    lint_packet: dict[str, Any],
    semantic_review: dict[str, Any],
    pages: dict[str, lint.Page],
    links: dict[str, Any],
) -> list[dict[str, Any]]:
    raw_by_id = {
        str(item.get("id") or item.get("candidate_id")): item
        for item in lint_packet.get("candidates", {}).get("synthesis_gap_candidates", []) or []
        if isinstance(item, dict)
    }
    covered = lint.existing_synthesis_pairs(pages, links)
    semantic_items = []
    findings = semantic_review.get("findings")
    if isinstance(findings, dict):
        semantic_items = [item for item in findings.get("synthesis_gaps", []) or [] if isinstance(item, dict)]
    source_items = semantic_items or list(raw_by_id.values())
    candidates: list[dict[str, Any]] = []
    seen_pairs: set[frozenset[str]] = set()
    reserved_paths: set[str] = set()
    for item in source_items:
        raw = raw_by_id.get(str(item.get("candidate_id") or item.get("id") or "")) or item
        pair = [str(value) for value in item.get("pair", raw.get("pair", [])) if str(value) in pages]
        if len(pair) != 2:
            continue
        pair_key = frozenset(pair)
        if pair_key in seen_pairs or pair_key in covered:
            continue
        seen_pairs.add(pair_key)
        left, right = sorted(pair)
        source_pages = [str(value) for value in item.get("evidence_pages", raw.get("source_pages", [])) if str(value) in pages]
        cooccurrence = int(raw.get("cooccurrence") or len(source_pages))
        score = int(raw.get("score") or cooccurrence)
        if item in semantic_items:
            score += {"high": 6, "medium": 4, "low": 2}.get(str(item.get("confidence") or ""), 1)
        title = f"{pages[left].title} × {pages[right].title}"
        target_path = unique_synthesis_path(pages, title, reserved_paths)
        reserved_paths.add(target_path)
        candidates.append(
            {
                "candidate_id": str(item.get("candidate_id") or raw.get("id") or f"synthesis-gap-{len(candidates) + 1}"),
                "pair": [left, right],
                "titles": [pages[left].title, pages[right].title],
                "suggested_title": title,
                "target_path": target_path,
                "cooccurrence": cooccurrence,
                "source_pages": source_pages,
                "score": score,
                "semantic_explanation": str(item.get("explanation") or ""),
                "confidence": str(item.get("confidence") or ""),
                "shared_tags": sorted(shared_domain_tags(pages[left], pages[right])),
                "cross_category": pages[left].category != pages[right].category,
            }
        )
    return sorted(candidates, key=lambda item: (-int(item["score"]), -int(item["cooccurrence"]), item["pair"]))


def filter_candidates(
    candidates: list[dict[str, Any]], pages: dict[str, lint.Page], workflow_args: list[str]
) -> list[dict[str, Any]]:
    terms = [term.lower() for term in re.findall(r"[A-Za-z0-9][A-Za-z0-9_-]*", " ".join(workflow_args))]
    if not terms:
        return candidates
    filtered = []
    for candidate in candidates:
        blobs = [candidate.get("candidate_id", ""), candidate.get("semantic_explanation", "")]
        for rel in [*candidate["pair"], *candidate.get("source_pages", [])]:
            page = pages.get(rel)
            if page:
                blobs.extend([page.rel, page.title, page.summary, " ".join(page.tags), " ".join(page.aliases)])
        haystack = " ".join(str(value).lower() for value in blobs)
        if all(term in haystack for term in terms):
            filtered.append(candidate)
    return filtered


def unique_synthesis_path(pages: dict[str, lint.Page], title: str, reserved: set[str] | None = None) -> str:
    base = slugify(title.replace("×", " x "))
    if not base:
        base = "synthesis"
    candidate = f"synthesis/{base}.md"
    existing = set(pages) | (reserved or set())
    suffix = 2
    while candidate in existing:
        candidate = f"synthesis/{base}-{suffix}.md"
        suffix += 1
    return candidate


def slugify(value: str) -> str:
    ascii_value = value.encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_value).strip("-")
    return re.sub(r"-{2,}", "-", slug)


def shared_domain_tags(left: lint.Page, right: lint.Page) -> set[str]:
    return {
        tag
        for tag in set(left.tags).intersection(right.tags)
        if tag and not tag.startswith("visibility/")
    }


def existing_synthesis_pages(pages: dict[str, lint.Page], links: dict[str, Any]) -> list[dict[str, Any]]:
    by_source: dict[str, list[str]] = defaultdict(list)
    for edge in links["resolved_edges"]:
        by_source[edge["source"]].append(edge["resolved"])
    result = []
    for page in pages.values():
        if not page.rel.startswith("synthesis/"):
            continue
        result.append(
            {
                "path": page.rel,
                "title": page.title,
                "summary": page.summary,
                "links": sorted(set(by_source.get(page.rel, []))),
            }
        )
    return result


def authoring_context(pages: dict[str, lint.Page], candidates: list[dict[str, Any]]) -> dict[str, Any]:
    needed: list[str] = []
    for candidate in candidates:
        needed.extend(candidate["pair"])
        needed.extend(candidate.get("source_pages", [])[:8])
    result = {}
    for rel in sorted(dict.fromkeys(needed)):
        page = pages.get(rel)
        if page is None:
            continue
        result[rel] = {
            "title": page.title,
            "summary": page.summary,
            "tags": page.tags,
            "base_confidence": page.base_confidence,
            "related_section": extract_related_section(page.body),
            "excerpt": trim(page.body, MAX_EXCERPT_CHARS),
        }
    return result


def extract_related_section(body: str) -> str:
    lines = body.splitlines()
    for index, line in enumerate(lines):
        if line.strip() == "## Related":
            end = len(lines)
            for cursor in range(index + 1, len(lines)):
                if lines[cursor].startswith("## "):
                    end = cursor
                    break
            return "\n".join(lines[index:end]).strip()
    return ""


def build_prompt(vault: Path, packet_path: Path, synthesis_plan_path: Path) -> str:
    return (
        "Use the `wiki-synthesize` skill to write synthesis content from this deterministic authoring packet.\n\n"
        f"Vault path: {vault}\n"
        f"Deterministic packet path: {packet_path}\n"
        f"Write synthesis plan JSON to: {synthesis_plan_path}\n\n"
        "Read the packet first. Do not edit wiki files. Write exactly one JSON object with this shape:\n"
        "{\n"
        '  "version": 1,\n'
        '  "status": "completed",\n'
        '  "decisions": [],\n'
        '  "hot_update": {"recent_activity": [], "active_threads": []},\n'
        '  "warnings": []\n'
        "}\n"
        "Each decision must include candidate_id and action. action must be create or skip. "
        "For create, include only summary, body, optional open_questions, and optional note. "
        "Do not include paths or frontmatter; the deterministic CLI owns all filesystem edits."
    )


def read_synthesis_plan(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SynthesizeError(f"could not read synthesis_plan.json: {exc}") from exc
    if not isinstance(data, dict):
        raise SynthesizeError("synthesis_plan must be a JSON object")
    return data


def validate_synthesis_plan(plan: dict[str, Any], packet: dict[str, Any], vault: Path) -> ValidatedSynthesisPlan:
    if plan.get("version") != 1:
        raise SynthesizeError("synthesis_plan version must be 1")
    if plan.get("status") != "completed":
        raise SynthesizeError("synthesis_plan status must be completed")
    raw_decisions = plan.get("decisions")
    if not isinstance(raw_decisions, list):
        raise SynthesizeError("synthesis_plan decisions must be a list")
    warnings = [str(item) for item in plan.get("warnings", [])] if isinstance(plan.get("warnings", []), list) else []
    candidates = {candidate["candidate_id"]: candidate for candidate in packet.get("candidates", [])}
    current_pages = lint.build_page_registry(vault)
    seen: set[str] = set()
    validated: list[ValidatedDecision] = []

    for decision in raw_decisions:
        if not isinstance(decision, dict):
            raise SynthesizeError("each decision must be an object")
        unexpected = set(decision) & {"path", "target_path", "frontmatter", "sources", "created", "updated"}
        if unexpected:
            raise SynthesizeError(f"decision includes deterministic fields: {', '.join(sorted(unexpected))}")
        candidate_id = str(decision.get("candidate_id") or "")
        if candidate_id in seen:
            raise SynthesizeError(f"duplicate decision for {candidate_id}")
        seen.add(candidate_id)
        candidate = candidates.get(candidate_id)
        if candidate is None:
            raise SynthesizeError(f"unknown candidate_id: {candidate_id}")
        action = str(decision.get("action") or "")
        if action not in VALID_ACTIONS:
            raise SynthesizeError(f"invalid action for {candidate_id}: {action}")
        for rel in candidate["pair"]:
            if rel not in current_pages:
                raise SynthesizeError(f"candidate page missing for {candidate_id}: {rel}")
        target_path = str(candidate["target_path"])
        if not target_path.startswith("synthesis/") or not target_path.endswith(".md"):
            raise SynthesizeError(f"invalid deterministic target path for {candidate_id}: {target_path}")
        if (vault / target_path).exists() and action != "skip":
            raise SynthesizeError(f"synthesis target already exists for {candidate_id}: {target_path}")
        if action == "skip":
            validated.append(ValidatedDecision(candidate=candidate, action=action, note=str(decision.get("note") or "")))
            continue
        summary = " ".join(str(decision.get("summary") or "").split())
        body = str(decision.get("body") or "").strip()
        if not summary:
            raise SynthesizeError(f"create decision missing summary for {candidate_id}")
        if len(summary) > 200:
            raise SynthesizeError(f"summary exceeds 200 characters for {candidate_id}")
        if not body:
            raise SynthesizeError(f"create decision missing body for {candidate_id}")
        if body.lstrip().startswith("---"):
            raise SynthesizeError(f"body must not include frontmatter for {candidate_id}")
        open_questions = as_text_list(decision.get("open_questions"))
        validated.append(
            ValidatedDecision(
                candidate=candidate,
                action=action,
                summary=summary,
                body=body,
                open_questions=open_questions,
                note=str(decision.get("note") or ""),
            )
        )
    return ValidatedSynthesisPlan(raw=plan, decisions=validated, warnings=warnings)


def apply_synthesis_plan(plan: ValidatedSynthesisPlan, vault: Path, config: dict[str, str]) -> dict[str, Any]:
    pages = lint.build_page_registry(vault)
    now = now_iso()
    today = now[:10]
    created: list[str] = []
    modified: set[str] = set()
    skipped = 0
    open_questions: list[str] = []

    for decision in plan.decisions:
        if decision.action == "skip":
            skipped += 1
            continue
        candidate = decision.candidate
        left_rel, right_rel = candidate["pair"]
        left = pages[left_rel]
        right = pages[right_rel]
        target = str(candidate["target_path"])
        title = str(candidate["suggested_title"])
        fm = {
            "title": title,
            "category": "synthesis",
            "tags": synthesis_tags(left, right),
            "sources": candidate.get("source_pages") or [left_rel, right_rel],
            "summary": decision.summary,
            "provenance": {"extracted": 0.2, "inferred": 0.7, "ambiguous": 0.1},
            "base_confidence": min_confidence(left.base_confidence, right.base_confidence),
            "lifecycle": "draft",
            "lifecycle_changed": today,
            "created": now,
            "updated": now,
        }
        body = normalize_body(
            title,
            decision.body,
            [(left_rel, left.title), (right_rel, right.title)],
            config,
        )
        path = vault / target
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render_page(fm, body), encoding="utf-8")
        created.append(target)
        for rel in [left_rel, right_rel]:
            if append_synthesis_backlink(vault / rel, rel, target, title, config):
                modified.add(rel)
        open_questions.extend(decision.open_questions or [])

    if created:
        rebuild_index(vault, config, now)
    return {
        "pages_created": created,
        "pages_modified": sorted(modified),
        "synthesis_created": len(created),
        "candidates_skipped": skipped,
        "open_questions": open_questions[:10],
    }


def synthesis_tags(left: lint.Page, right: lint.Page) -> list[str]:
    tags = sorted(shared_domain_tags(left, right))
    if tags:
        return tags[:5]
    combined = []
    for tag in [*left.tags, *right.tags]:
        if tag.startswith("visibility/") or tag in combined:
            continue
        combined.append(tag)
    return combined[:5] or ["synthesis"]


def min_confidence(*values: Any) -> float:
    numeric = [float(value) for value in values if isinstance(value, (int, float))]
    return min(numeric) if numeric else 0.4


def normalize_body(title: str, body: str, pair: list[tuple[str, str]], config: dict[str, str]) -> str:
    text = body.strip()
    if not text.startswith("# "):
        text = f"# {title}\n\n{text}"
    related_links = [format_link(Path("synthesis/placeholder.md"), Path(rel), display, config) for rel, display in pair]
    if "## Related" not in text:
        text = text.rstrip() + "\n\n## Related\n" + "\n".join(f"- {link}" for link in related_links)
    return text


def append_synthesis_backlink(path: Path, source_rel: str, target: str, title: str, config: dict[str, str]) -> bool:
    text = path.read_text(encoding="utf-8")
    link = format_link(Path(source_rel), Path(target), title, config)
    if link in text:
        return False
    lines = text.rstrip().splitlines()
    try:
        heading_index = next(index for index, line in enumerate(lines) if line.strip() == "## Related")
    except StopIteration:
        lines.extend(["", "## Related", ""])
        heading_index = len(lines) - 2
    insert_at = len(lines)
    for index in range(heading_index + 1, len(lines)):
        if lines[index].startswith("## "):
            insert_at = index
            break
    lines[insert_at:insert_at] = [f"- {link} - synthesis"] + ([] if insert_at == len(lines) else [""])
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return True


def format_link(current: Path, target: Path, display: str, config: dict[str, str]) -> str:
    if config.get("OBSIDIAN_LINK_FORMAT", "wikilink") == "markdown":
        rel = os.path.relpath(target, start=current.parent)
        return f"[{display}]({rel})"
    no_ext = target.with_suffix("").as_posix()
    natural = display.strip()
    if natural == Path(no_ext).name or natural == no_ext:
        return f"[[{no_ext}]]"
    return f"[[{no_ext}|{natural}]]"


def append_log(vault: Path, packet: dict[str, Any], applied: dict[str, Any]) -> None:
    line = (
        f"- [{now_iso()}] WIKI_SYNTHESIZE pages_scanned={packet.get('summary', {}).get('pages_scanned', 0)} "
        f"synthesis_created={applied['synthesis_created']} "
        f"candidates_skipped={len(packet.get('opportunities', [])) + applied['candidates_skipped']}\n"
    )
    path = vault / "log.md"
    if path.exists():
        ensure_managed_tag(path, "Wiki Log")
        path.write_text(path.read_text(encoding="utf-8").rstrip() + "\n" + line, encoding="utf-8")
    else:
        path.write_text(f"---\ntitle: Wiki Log\ntags: {managed_tags()}\n---\n\n# Wiki Log\n\n" + line, encoding="utf-8")


def write_hot(vault: Path, plan: ValidatedSynthesisPlan, applied: dict[str, Any]) -> None:
    now = now_iso()
    hot = plan.raw.get("hot_update") if isinstance(plan.raw.get("hot_update"), dict) else {}
    recent = as_text_list(hot.get("recent_activity")) or [
        f"Synthesized {applied['synthesis_created']} cross-cutting pages: {', '.join(Path(path).stem for path in applied['pages_created'])}."
    ]
    active = as_text_list(hot.get("active_threads")) or applied.get("open_questions", [])
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
        *[f"- {item}" for item in recent[:3]],
        "",
        "## Active Threads",
        *[f"- {item}" for item in (active[:5] or ["None."])],
        "",
        "## Key Takeaways",
        "- None.",
        "",
        "## Flagged Contradictions",
        "- None.",
        "",
    ]
    (vault / "hot.md").write_text("\n".join(content), encoding="utf-8")


def update_manifest_stats(vault: Path) -> None:
    manifest, _exists = read_manifest(vault)
    manifest.setdefault("version", 1)
    manifest.setdefault("sources", {})
    manifest.setdefault("projects", {})
    stats = manifest.get("stats") if isinstance(manifest.get("stats"), dict) else {}
    stats["total_sources_ingested"] = len(manifest.get("sources", {}) if isinstance(manifest.get("sources"), dict) else {})
    stats["total_pages"] = count_wiki_pages(vault)
    stats["total_projects"] = len(manifest.get("projects", {}) if isinstance(manifest.get("projects"), dict) else {})
    manifest["stats"] = stats
    manifest["last_updated"] = now_iso()
    write_json(vault / ".manifest.json", manifest)


def build_report(
    packet: dict[str, Any],
    *,
    status: str,
    validated: ValidatedSynthesisPlan | None = None,
    applied: dict[str, Any] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    decisions = validated.decisions if validated else []
    created = applied.get("pages_created", []) if applied else []
    return {
        "version": 1,
        "status": status,
        "generated_at": now_iso(),
        "vault": packet.get("vault"),
        "summary": {
            "candidates": len(packet.get("candidates", [])),
            "opportunities": len(packet.get("opportunities", [])),
            "decisions": len(decisions),
            "create_decisions": sum(1 for decision in decisions if decision.action == "create"),
            "skip_decisions": sum(1 for decision in decisions if decision.action == "skip"),
            "synthesis_created": len(created),
            "pages_modified": len(applied.get("pages_modified", [])) if applied else 0,
        },
        "pages_created": created,
        "pages_modified": applied.get("pages_modified", []) if applied else [],
        "warnings": warnings or [],
        "candidate_sample": packet.get("candidates", [])[:20],
        "opportunities": packet.get("opportunities", [])[:20],
    }


def print_report(report: dict[str, Any], *, json_output: bool) -> None:
    if json_output:
        print(json.dumps(report, indent=2, sort_keys=True))
        return
    summary = report["summary"]
    lines = [
        "## Wiki Synthesize Report",
        "",
        f"- **Status:** {report['status']}",
        f"- **Candidates:** {summary['candidates']}",
        f"- **Decisions:** {summary['decisions']}",
        f"- **Synthesis created:** {summary['synthesis_created']}",
        f"- **Pages modified:** {summary['pages_modified']}",
        f"- **Skipped opportunities:** {summary['opportunities']}",
    ]
    if report.get("warnings"):
        lines.extend(["", "### Warnings", ""])
        lines.extend(f"- {warning}" for warning in report["warnings"])
    print("\n".join(lines).rstrip() + "\n")


def create_run(vault: Path) -> Run:
    candidate = timestamped_run_dir(vault, "synthesize")
    return Run(
        run_dir=candidate,
        packet_path=candidate / "packet.json",
        synthesis_plan_path=candidate / "synthesis_plan.json",
        report_path=candidate / "report.json",
    )


def trim(value: str, max_chars: int) -> str:
    text = value.strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 15].rstrip() + "\n[... truncated]"


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
