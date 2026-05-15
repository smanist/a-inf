from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from a_inf import lint
from a_inf.ingest import load_wiki_config, parse_frontmatter_file, read_optional, render_frontmatter, write_json
from a_inf.managed_files import A_INF_TAG, ensure_managed_tag, managed_tags
from a_inf.qmd import ensure_qmd_collection, qmd_env, sync_qmd
from a_inf.runs import runs_root, timestamped_run_dir


VALID_ACTIONS = {"update", "skip", "add_taxonomy_tag"}
VALID_VISIBILITY_TAGS = {"visibility/public", "visibility/internal", "visibility/pii"}
TAG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*(?:/[a-z0-9][a-z0-9-]*)?$")


class TagsError(Exception):
    pass


@dataclass(frozen=True)
class Run:
    run_dir: Path
    packet_path: Path
    tag_plan_path: Path
    report_path: Path


@dataclass(frozen=True)
class ValidatedDecision:
    action: str
    page: str = ""
    expected_tags: tuple[str, ...] = ()
    proposed_tags: tuple[str, ...] = ()
    reason: str = ""
    tag: str = ""
    aliases: tuple[str, ...] = ()
    section: str = "Suggested Tags"


@dataclass(frozen=True)
class ValidatedTagPlan:
    raw: dict[str, Any]
    decisions: list[ValidatedDecision]
    warnings: list[str]


def run_tags(args: Any, vault: Path, config: dict[str, str] | None = None) -> int:
    config = config or load_wiki_config(vault)
    if getattr(args, "fix", False):
        return run_fix(args, vault, config)

    run = create_run(vault)
    packet = build_tag_packet(vault, config, run.tag_plan_path)
    write_json(run.packet_path, packet)

    if getattr(args, "print_prompt", False):
        print(build_prompt(vault, run.packet_path, run.tag_plan_path))
        return 0

    if getattr(args, "no_codex", False):
        report = build_report(packet, status="not_run", warnings=["tag planning skipped by --no-codex"])
        write_json(run.report_path, report)
        print_report(report, json_output=getattr(args, "json", False))
        return 0

    codex_bin = shutil.which(getattr(args, "codex_bin", "codex"))
    if codex_bin is None:
        print("Codex executable not found. Re-run with --print-prompt or install Codex CLI.", file=sys.stderr)
        print(build_prompt(vault, run.packet_path, run.tag_plan_path))
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
    command.append(build_prompt(vault, run.packet_path, run.tag_plan_path))
    result = subprocess.call(command, cwd=vault, env=qmd_env(os.environ, vault))
    if result != 0:
        return result

    try:
        raw_plan = read_tag_plan(run.tag_plan_path)
        validated = validate_tag_plan(raw_plan, vault)
        report = build_report(packet, status="planned", validated=validated)
        write_json(run.report_path, report)
        print_report(report, json_output=getattr(args, "json", False))
        return 0
    except TagsError as exc:
        report = build_report(packet, status="invalid", warnings=[str(exc)])
        write_json(run.report_path, report)
        print_report(report, json_output=getattr(args, "json", False))
        return 1


def run_fix(args: Any, vault: Path, config: dict[str, str]) -> int:
    plan_path = Path(getattr(args, "plan", "") or "") if getattr(args, "plan", None) else latest_plan_path(vault)
    if plan_path is None:
        print("No tag plan found. Run `a-inf tags` first or pass `--plan <path>`.", file=sys.stderr)
        return 1
    if not plan_path.is_absolute():
        plan_path = (vault / plan_path).resolve()
    try:
        raw_plan = read_tag_plan(plan_path)
        validated = validate_tag_plan(raw_plan, vault)
        applied = apply_tag_plan(validated, vault)
        packet = build_tag_packet(vault, config, plan_path)
        warnings = [*validated.warnings]
        changed = bool(applied["pages_modified"] or applied["new_tags_added"])
        if changed and not getattr(args, "no_log", False):
            append_log(vault, applied)
            write_hot(vault, applied)
        if changed and getattr(args, "sandbox", "workspace-write") != "read-only":
            if ensure_qmd_collection(vault, config) and not sync_qmd(vault, config):
                warnings.append("QMD sync failed after tag normalization; wiki files were still updated.")
        report = build_report(packet, status="completed", validated=validated, applied=applied, warnings=warnings)
        print_report(report, json_output=getattr(args, "json", False))
        return 0
    except TagsError as exc:
        packet = build_tag_packet(vault, config, plan_path)
        report = build_report(packet, status="invalid", warnings=[str(exc)])
        print_report(report, json_output=getattr(args, "json", False))
        return 1


def build_tag_packet(vault: Path, config: dict[str, str] | None, tag_plan_path: Path) -> dict[str, Any]:
    pages = lint.build_page_registry(vault)
    taxonomy = parse_taxonomy(vault / "_meta" / "taxonomy.md")
    canonical = set(taxonomy["canonical_tags"])
    aliases = dict(taxonomy["aliases"])
    frequencies: Counter[str] = Counter()
    page_summaries: dict[str, dict[str, Any]] = {}
    duplicate_tags: list[dict[str, Any]] = []
    malformed_tags: list[dict[str, Any]] = []
    alias_tags: list[dict[str, Any]] = []
    unknown_tags: list[dict[str, Any]] = []
    over_tagged_pages: list[dict[str, Any]] = []
    untagged_pages: list[dict[str, Any]] = []
    visibility_issues: list[dict[str, Any]] = []

    for rel, page in sorted(pages.items()):
        tags = [tag.lstrip("#") for tag in page.tags]
        page_summaries[rel] = {
            "title": page.title,
            "category": page.category,
            "tags": tags,
            "summary": page.summary,
        }
        if not tags:
            untagged_pages.append({"page": rel, "message": "page has no tags"})
        frequencies.update(tags)
        counts = Counter(tags)
        duplicates = sorted(tag for tag, count in counts.items() if count > 1)
        if duplicates:
            duplicate_tags.append({"page": rel, "tags": duplicates, "message": "duplicate tags"})
        non_visibility = [tag for tag in tags if not tag.startswith("visibility/")]
        if len(non_visibility) > 5:
            over_tagged_pages.append({"page": rel, "tag_count": len(non_visibility), "tags": non_visibility})
        visibility_tags = [tag for tag in tags if tag.startswith("visibility/")]
        bad_visibility = [tag for tag in visibility_tags if tag not in VALID_VISIBILITY_TAGS]
        if bad_visibility:
            visibility_issues.append({"page": rel, "tags": bad_visibility, "message": "invalid visibility tag"})
        if len(set(visibility_tags)) > 1:
            visibility_issues.append({"page": rel, "tags": sorted(set(visibility_tags)), "message": "multiple visibility tags"})
        for tag in tags:
            normalized = normalize_tag(tag)
            if normalized != tag or not is_valid_tag(tag):
                malformed_tags.append(
                    {
                        "page": rel,
                        "tag": tag,
                        "suggested": normalized if is_valid_tag(normalized) else "",
                        "message": "tag is not lowercase/kebab-case",
                    }
                )
            if tag.startswith("visibility/"):
                continue
            if tag in aliases:
                alias_tags.append({"page": rel, "tag": tag, "canonical": aliases[tag]})
            elif tag not in canonical:
                unknown_tags.append({"page": rel, "tag": tag})

    unknown_counts = Counter(item["tag"] for item in unknown_tags)
    alias_counts = Counter((item["tag"], item["canonical"]) for item in alias_tags)
    return {
        "version": 1,
        "generated_at": now_iso(),
        "vault": str(vault),
        "link_format": (config or {}).get("OBSIDIAN_LINK_FORMAT", "wikilink"),
        "tag_plan_path": str(tag_plan_path),
        "taxonomy": taxonomy,
        "summary": {
            "pages_scanned": len(pages),
            "unique_tags": len(frequencies),
            "canonical_tags_used": sum(1 for tag in frequencies if tag in canonical),
            "alias_tags_found": len(alias_tags),
            "unknown_tags_found": len(unknown_tags),
            "pages_over_tag_limit": len(over_tagged_pages),
            "untagged_pages": len(untagged_pages),
            "duplicate_tag_pages": len(duplicate_tags),
            "malformed_tags": len(malformed_tags),
            "visibility_issues": len(visibility_issues),
        },
        "tag_frequencies": [
            {"tag": tag, "count": count}
            for tag, count in sorted(frequencies.items(), key=lambda item: (-item[1], item[0]))
        ],
        "page_registry": page_summaries,
        "findings": {
            "alias_tags": [
                {"tag": tag, "canonical": canonical_tag, "pages": count}
                for (tag, canonical_tag), count in sorted(alias_counts.items())
            ],
            "unknown_tags": [
                {
                    "tag": tag,
                    "pages": count,
                    "recommendation": "consider adding to taxonomy" if count >= 2 else "map to closest canonical tag or skip",
                }
                for tag, count in sorted(unknown_counts.items())
            ],
            "duplicate_tags": duplicate_tags,
            "malformed_tags": malformed_tags,
            "over_tagged_pages": over_tagged_pages,
            "untagged_pages": untagged_pages,
            "visibility_issues": visibility_issues,
        },
    }


def parse_taxonomy(path: Path) -> dict[str, Any]:
    text = read_optional(path)
    if not text:
        return {"path": str(path), "canonical_tags": [A_INF_TAG], "aliases": {}, "raw_excerpt": ""}
    body = lint.strip_frontmatter(text)
    canonical: set[str] = {A_INF_TAG}
    aliases: dict[str, str] = {}
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        backticked = [token for token in re.findall(r"`([^`]+)`", stripped) if is_valid_tag(token)]
        arrow = re.search(r"`([^`]+)`\s*(?:->|→)\s*`([^`]+)`", stripped)
        if arrow and is_valid_tag(arrow.group(1)) and is_valid_tag(arrow.group(2)):
            old = arrow.group(1)
            new = arrow.group(2)
            if not old.startswith("visibility/") and not new.startswith("visibility/"):
                canonical.add(new)
                aliases[old] = new
            continue
        if not backticked:
            continue
        if "alias" in stripped.lower() and len(backticked) >= 2:
            canon = backticked[0]
            if not canon.startswith("visibility/"):
                canonical.add(canon)
                for alias in backticked[1:]:
                    if not alias.startswith("visibility/"):
                        aliases[alias] = canon
            continue
        if stripped.startswith(("-", "*", "|")):
            tag = backticked[0]
            if not tag.startswith("visibility/"):
                canonical.add(tag)
    return {
        "path": str(path),
        "canonical_tags": sorted(canonical),
        "aliases": dict(sorted(aliases.items())),
        "raw_excerpt": trim(body, 2400),
    }


def build_prompt(vault: Path, packet_path: Path, tag_plan_path: Path) -> str:
    return (
        "Use the `wiki-tags` skill to review this deterministic tag audit packet.\n\n"
        f"Vault path: {vault}\n"
        f"Deterministic packet path: {packet_path}\n"
        f"Write editable tag plan JSON to: {tag_plan_path}\n\n"
        "Read the packet first. Do not edit wiki pages or taxonomy files. Write exactly one JSON object with this shape:\n"
        "{\n"
        '  "version": 1,\n'
        '  "status": "completed",\n'
        '  "decisions": [\n'
        '    {"action": "update", "page": "concepts/example.md", "expected_tags": ["old"], "proposed_tags": ["new"], "reason": "why"},\n'
        '    {"action": "skip", "page": "concepts/example.md", "expected_tags": ["tag"], "reason": "why"},\n'
        '    {"action": "add_taxonomy_tag", "tag": "new-tag", "aliases": [], "section": "Suggested Tags", "reason": "why"}\n'
        "  ],\n"
        '  "warnings": []\n'
        "}\n"
        "Use only actions update, skip, or add_taxonomy_tag. For update decisions, include the page's exact "
        "current expected_tags from the packet and proposed_tags with no duplicate tags, no more than five "
        "non-visibility tags, and at most one visibility tag. Preserve visibility tags unless correcting an "
        "invalid or duplicate visibility tag. Unknown tag mappings are suggestions; the user may edit this plan "
        "before running `a-inf tags --fix`."
    )


def read_tag_plan(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TagsError(f"could not read tag_plan.json: {exc}") from exc
    if not isinstance(data, dict):
        raise TagsError("tag_plan must be a JSON object")
    return data


def validate_tag_plan(plan: dict[str, Any], vault: Path) -> ValidatedTagPlan:
    if plan.get("version") != 1:
        raise TagsError("tag_plan version must be 1")
    if plan.get("status") != "completed":
        raise TagsError("tag_plan status must be completed")
    raw_decisions = plan.get("decisions")
    if not isinstance(raw_decisions, list):
        raise TagsError("tag_plan decisions must be a list")
    warnings = [str(item) for item in plan.get("warnings", [])] if isinstance(plan.get("warnings", []), list) else []
    pages = lint.build_page_registry(vault)
    seen_pages: set[str] = set()
    validated: list[ValidatedDecision] = []
    for raw in raw_decisions:
        if not isinstance(raw, dict):
            raise TagsError("each tag decision must be an object")
        action = str(raw.get("action") or "")
        if action not in VALID_ACTIONS:
            raise TagsError(f"invalid tag action: {action}")
        if action == "add_taxonomy_tag":
            tag = str(raw.get("tag") or "")
            if not is_valid_tag(tag) or tag.startswith("visibility/"):
                raise TagsError(f"invalid taxonomy tag: {tag}")
            aliases = tuple(str(item) for item in raw.get("aliases", []) if str(item)) if isinstance(raw.get("aliases", []), list) else ()
            for alias in aliases:
                if not is_valid_tag(alias) or alias.startswith("visibility/"):
                    raise TagsError(f"invalid alias for {tag}: {alias}")
            validated.append(
                ValidatedDecision(
                    action=action,
                    tag=tag,
                    aliases=aliases,
                    section=str(raw.get("section") or "Suggested Tags"),
                    reason=str(raw.get("reason") or ""),
                )
            )
            continue

        page = str(raw.get("page") or "")
        if page not in pages:
            raise TagsError(f"unknown page in tag decision: {page}")
        if page in seen_pages:
            raise TagsError(f"duplicate tag decision for page: {page}")
        seen_pages.add(page)
        expected = tuple(as_string_list(raw.get("expected_tags"), f"expected_tags for {page}"))
        current = tuple(tag.lstrip("#") for tag in pages[page].tags)
        if current != expected:
            raise TagsError(f"current tags changed for {page}: expected {list(expected)}, found {list(current)}")
        if action == "skip":
            validated.append(
                ValidatedDecision(action=action, page=page, expected_tags=expected, reason=str(raw.get("reason") or ""))
            )
            continue
        proposed = tuple(as_string_list(raw.get("proposed_tags"), f"proposed_tags for {page}"))
        validate_tags(proposed, page)
        validated.append(
            ValidatedDecision(
                action=action,
                page=page,
                expected_tags=expected,
                proposed_tags=proposed,
                reason=str(raw.get("reason") or ""),
            )
        )
    return ValidatedTagPlan(raw=plan, decisions=validated, warnings=warnings)


def validate_tags(tags: tuple[str, ...], page: str) -> None:
    if len(tags) != len(set(tags)):
        raise TagsError(f"proposed_tags for {page} contains duplicate tags")
    visibility = [tag for tag in tags if tag.startswith("visibility/")]
    invalid_visibility = [tag for tag in visibility if tag not in VALID_VISIBILITY_TAGS]
    if invalid_visibility:
        raise TagsError(f"proposed_tags for {page} contains invalid visibility tags: {invalid_visibility}")
    if len(visibility) > 1:
        raise TagsError(f"proposed_tags for {page} contains multiple visibility tags")
    non_visibility = [tag for tag in tags if not tag.startswith("visibility/")]
    if len(non_visibility) > 5:
        raise TagsError(f"proposed_tags for {page} has more than 5 non-visibility tags")
    for tag in tags:
        if not is_valid_tag(tag):
            raise TagsError(f"proposed_tags for {page} contains invalid tag: {tag}")


def apply_tag_plan(plan: ValidatedTagPlan, vault: Path) -> dict[str, Any]:
    pages_modified: list[str] = []
    tags_renamed = 0
    now = now_iso()
    for decision in plan.decisions:
        if decision.action != "update":
            continue
        if decision.expected_tags == decision.proposed_tags:
            continue
        path = vault / decision.page
        fm = parse_frontmatter_file(path)
        old_tags = [str(tag).lstrip("#") for tag in fm.get("tags", [])] if isinstance(fm.get("tags"), list) else list(decision.expected_tags)
        fm["tags"] = list(decision.proposed_tags)
        fm["updated"] = now
        body = lint.strip_frontmatter(path.read_text(encoding="utf-8"))
        path.write_text("---\n" + render_frontmatter(fm) + "---\n\n" + body.strip() + "\n", encoding="utf-8")
        pages_modified.append(decision.page)
        tags_renamed += count_changed_tags(old_tags, list(decision.proposed_tags))
    new_tags_added = append_taxonomy_tags(vault, plan)
    return {
        "pages_modified": sorted(pages_modified),
        "tags_renamed": tags_renamed,
        "new_tags_added": new_tags_added,
        "decisions_applied": len([decision for decision in plan.decisions if decision.action != "skip"]),
    }


def append_taxonomy_tags(vault: Path, plan: ValidatedTagPlan) -> int:
    additions = [decision for decision in plan.decisions if decision.action == "add_taxonomy_tag"]
    if not additions:
        return 0
    path = vault / "_meta" / "taxonomy.md"
    current = read_optional(path) or "# Tag Taxonomy\n"
    taxonomy = parse_taxonomy(path)
    existing = set(taxonomy["canonical_tags"])
    lines: list[str] = []
    for decision in additions:
        if decision.tag in existing:
            continue
        existing.add(decision.tag)
        alias_text = f" Aliases: {', '.join(f'`{alias}`' for alias in decision.aliases)}." if decision.aliases else ""
        reason = f" - {decision.reason}" if decision.reason else ""
        lines.append(f"- `{decision.tag}`{alias_text}{reason}".rstrip())
    if not lines:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(current.rstrip() + "\n\n## Suggested Tags\n\n" + "\n".join(lines) + "\n", encoding="utf-8")
    return len(lines)


def latest_plan_path(vault: Path) -> Path | None:
    runs = runs_root(vault)
    if not runs.exists():
        return None
    for run_dir in sorted(runs.glob("tags-*"), key=lambda path: path.name, reverse=True):
        plan_path = run_dir / "tag_plan.json"
        if plan_path.exists():
            return plan_path
    return None


def create_run(vault: Path) -> Run:
    candidate = timestamped_run_dir(vault, "tags")
    return Run(
        run_dir=candidate,
        packet_path=candidate / "packet.json",
        tag_plan_path=candidate / "tag_plan.json",
        report_path=candidate / "report.json",
    )


def build_report(
    packet: dict[str, Any],
    *,
    status: str,
    validated: ValidatedTagPlan | None = None,
    applied: dict[str, Any] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "version": 1,
        "status": status,
        "summary": {
            **packet.get("summary", {}),
            "decisions": len(validated.decisions) if validated else 0,
            "pages_modified": len((applied or {}).get("pages_modified", [])),
            "new_tags_added": (applied or {}).get("new_tags_added", 0),
        },
        "tag_plan_path": packet.get("tag_plan_path"),
        "findings": packet.get("findings", {}),
        "applied": applied or {"pages_modified": [], "tags_renamed": 0, "new_tags_added": 0, "decisions_applied": 0},
        "warnings": warnings or ([] if validated is None else validated.warnings),
    }


def print_report(report: dict[str, Any], *, json_output: bool) -> None:
    if json_output:
        print(json.dumps(report, indent=2, sort_keys=True))
        return
    summary = report["summary"]
    lines = [
        "## Tag Audit Report",
        "",
        f"- **Status:** {report['status']}",
        f"- **Pages scanned:** {summary.get('pages_scanned', 0)}",
        f"- **Unique tags:** {summary.get('unique_tags', 0)}",
        f"- **Alias tags found:** {summary.get('alias_tags_found', 0)}",
        f"- **Unknown tags found:** {summary.get('unknown_tags_found', 0)}",
        f"- **Pages over tag limit:** {summary.get('pages_over_tag_limit', 0)}",
        f"- **Untagged pages:** {summary.get('untagged_pages', 0)}",
        f"- **Duplicate tag pages:** {summary.get('duplicate_tag_pages', 0)}",
        f"- **Malformed tags:** {summary.get('malformed_tags', 0)}",
        f"- **Visibility issues:** {summary.get('visibility_issues', 0)}",
        f"- **Editable plan:** {report.get('tag_plan_path') or '-'}",
        "",
    ]
    for title, key in [
        ("Alias Tags", "alias_tags"),
        ("Unknown Tags", "unknown_tags"),
        ("Duplicate Tags", "duplicate_tags"),
        ("Malformed Tags", "malformed_tags"),
        ("Over-Tagged Pages", "over_tagged_pages"),
        ("Untagged Pages", "untagged_pages"),
        ("Visibility Issues", "visibility_issues"),
    ]:
        lines.extend(render_section(title, report.get("findings", {}).get(key, [])))
    warnings = report.get("warnings") or []
    if warnings:
        lines.extend(["### Warnings", ""])
        lines.extend(f"- {warning}" for warning in warnings)
        lines.append("")
    print("\n".join(lines).rstrip() + "\n")


def render_section(title: str, items: list[Any]) -> list[str]:
    lines = [f"### {title} ({len(items)} found)", ""]
    if not items:
        lines.extend(["_None._", ""])
        return lines
    lines.extend(f"- {format_item(item)}" for item in items[:20])
    if len(items) > 20:
        lines.append(f"\n_Showing 20 of {len(items)}._")
    lines.append("")
    return lines


def format_item(item: Any) -> str:
    if not isinstance(item, dict):
        return str(item)
    subject = item.get("page") or item.get("tag") or "tag"
    message = item.get("message") or item.get("recommendation") or item.get("canonical") or ""
    extras = []
    for key in ["pages", "tag_count", "tags", "suggested"]:
        if key in item:
            extras.append(f"{key}={item[key]}")
    suffix = f" ({', '.join(extras)})" if extras else ""
    return f"`{subject}` - {message}{suffix}" if message else f"`{subject}`{suffix}"


def append_log(vault: Path, applied: dict[str, Any]) -> None:
    line = (
        f"- [{now_iso()}] TAG_NORMALIZE tags_renamed={applied['tags_renamed']} "
        f"pages_modified={len(applied['pages_modified'])} new_tags_added={applied['new_tags_added']}\n"
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
        f"Tag normalization updated {len(applied['pages_modified'])} pages; "
        f"{applied['tags_renamed']} tag edits and {applied['new_tags_added']} taxonomy additions."
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
        content[9:10] = [f"- {recent_line}", *activity[:2]]
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


def as_string_list(value: Any, field: str) -> list[str]:
    if not isinstance(value, list):
        raise TagsError(f"{field} must be a list")
    return [str(item).lstrip("#") for item in value if str(item)]


def is_valid_tag(tag: str) -> bool:
    return bool(TAG_PATTERN.match(tag))


def normalize_tag(tag: str) -> str:
    stripped = tag.strip().lstrip("#").lower().replace("_", "-")
    stripped = re.sub(r"\s+", "-", stripped)
    stripped = re.sub(r"[^a-z0-9/-]+", "-", stripped)
    stripped = re.sub(r"-+", "-", stripped).strip("-/")
    return stripped


def count_changed_tags(old: list[str], new: list[str]) -> int:
    return len(set(old).symmetric_difference(new))


def trim(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 20].rstrip() + "\n...[truncated]"


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
