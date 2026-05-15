from __future__ import annotations

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

from a_inf.ingest import load_wiki_config, parse_frontmatter_file, read_optional, write_json
from a_inf.managed_files import A_INF_TAG
from a_inf.qmd import collection_name_for_vault, qmd_env, resolve_qmd, run_qmd
from a_inf.runs import timestamped_run_dir


WIKI_PAGE_DIRS = ["concepts", "entities", "skills", "references", "synthesis", "journal", "projects"]
MAX_BODY_CHARS = 1600
MAX_SUPPORT_CHARS = 2200
MAX_AUTO_CONTEXT = 5


@dataclass(frozen=True)
class Run:
    run_dir: Path
    packet_path: Path
    report_path: Path
    output_path: Path


@dataclass(frozen=True)
class Page:
    rel: str
    path: Path
    title: str
    category: str
    tags: list[str]
    summary: str
    body: str


def run_ideate(args: Any, vault: Path, config: dict[str, str] | None = None) -> int:
    config = config or load_wiki_config(vault)
    idea = " ".join(getattr(args, "args", [])).strip()
    if not idea:
        print("a-inf ideate requires an idea.", file=sys.stderr)
        return 2

    run = create_run(vault, idea)
    packet = build_ideation_packet(vault, config, idea, getattr(args, "entry", []) or [], run.output_path)
    write_json(run.packet_path, packet)

    prompt = build_prompt(vault, run.packet_path, run.output_path)
    if getattr(args, "print_prompt", False):
        print(prompt)
        return 0

    if getattr(args, "no_codex", False):
        report = build_report(
            packet,
            status="not_run",
            warnings=[*packet.get("warnings", []), "ideation skipped by --no-codex"],
        )
        write_json(run.report_path, report)
        print_report(report, json_output=getattr(args, "json", False))
        return 0

    codex_bin = shutil.which(getattr(args, "codex_bin", "codex"))
    if codex_bin is None:
        print("Codex executable not found. Re-run with --print-prompt or install Codex CLI.", file=sys.stderr)
        print(prompt)
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
    command.append(prompt)

    result = subprocess.call(command, cwd=vault, env=qmd_env(os.environ, vault))
    if result != 0:
        return result

    warnings = list(packet.get("warnings", []))
    valid, message = validate_output(run.output_path)
    if not valid:
        report = build_report(packet, status="invalid", warnings=[*warnings, message])
        write_json(run.report_path, report)
        print_report(report, json_output=getattr(args, "json", False))
        return 1

    report = build_report(packet, status="completed", warnings=warnings)
    write_json(run.report_path, report)
    print_report(report, json_output=getattr(args, "json", False))
    return 0


def build_ideation_packet(
    vault: Path,
    config: dict[str, str],
    idea: str,
    entries: list[str],
    output_path: Path,
) -> dict[str, Any]:
    pages = build_page_registry(vault)
    warnings: list[str] = []
    explicit_context, entry_warnings = resolve_entries(entries, pages)
    warnings.extend(entry_warnings)
    auto_context, auto_warnings = auto_nearby_context(vault, config, idea, pages, {page["path"] for page in explicit_context})
    warnings.extend(auto_warnings)
    return {
        "version": 1,
        "generated_at": now_iso(),
        "vault": str(vault),
        "idea": idea,
        "output_path": str(output_path),
        "output_relpath": output_path.relative_to(vault).as_posix(),
        "link_format": config.get("OBSIDIAN_LINK_FORMAT") or config.get("link_format") or "wikilink",
        "requested_entries": entries,
        "explicit_context": explicit_context,
        "auto_context": auto_context,
        "support_context": {
            "agents": trim(read_optional(vault / "AGENTS.md"), MAX_SUPPORT_CHARS),
            "hot": trim(read_optional(vault / "hot.md"), MAX_SUPPORT_CHARS),
            "index": trim(read_optional(vault / "index.md"), MAX_SUPPORT_CHARS),
        },
        "warnings": warnings,
        "instructions": {
            "artifact_kind": "idea_packet",
            "artifact_tag": A_INF_TAG,
            "required_sections": [
                "Raw Idea",
                "Distilled Claim",
                "Relevant Context",
                "Mathematical Sketch",
                "Agent Handoff",
                "Open Questions",
            ],
            "do_not_update": [".manifest.json", "index.md", "log.md", "hot.md", "QMD"],
        },
    }


def build_page_registry(vault: Path) -> dict[str, Page]:
    pages: dict[str, Page] = {}
    for dirname in WIKI_PAGE_DIRS:
        root = vault / dirname
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.md")):
            if not path.is_file():
                continue
            rel = path.relative_to(vault).as_posix()
            text = read_optional(path)
            fm = parse_frontmatter_file(path)
            tags = frontmatter_tags(path, fm)
            if A_INF_TAG in tags:
                continue
            pages[rel] = Page(
                rel=rel,
                path=path,
                title=str(fm.get("title") or path.stem),
                category=str(fm.get("category") or dirname),
                tags=tags,
                summary=str(fm.get("summary") or ""),
                body=strip_frontmatter(text),
            )
    return pages


def resolve_entries(entries: list[str], pages: dict[str, Page]) -> tuple[list[dict[str, Any]], list[str]]:
    resolved: list[dict[str, Any]] = []
    warnings: list[str] = []
    seen: set[str] = set()
    for raw in entries:
        page = resolve_entry(raw, pages)
        if page is None:
            warnings.append(f"entry not found: {raw}")
            continue
        if page.rel in seen:
            continue
        seen.add(page.rel)
        resolved.append(page_context(page, reason="explicit"))
    return resolved, warnings


def resolve_entry(raw: str, pages: dict[str, Page]) -> Page | None:
    key = normalize_entry_key(raw)
    candidates = [key]
    if key and not key.endswith(".md"):
        candidates.append(f"{key}.md")
    for candidate in candidates:
        if candidate in pages:
            return pages[candidate]

    lowered = normalize_lookup(key)
    for page in pages.values():
        aliases = {
            normalize_lookup(page.title),
            normalize_lookup(Path(page.rel).stem),
            normalize_lookup(page.rel),
            normalize_lookup(page.rel.removesuffix(".md")),
        }
        if lowered in aliases:
            return page
    return None


def normalize_entry_key(raw: str) -> str:
    value = raw.strip()
    if value.startswith("[[") and value.endswith("]]"):
        value = value[2:-2]
    value = value.split("|", 1)[0].split("#", 1)[0].strip()
    return value.strip("/").removeprefix("./")


def normalize_lookup(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


def auto_nearby_context(
    vault: Path,
    config: dict[str, str],
    idea: str,
    pages: dict[str, Page],
    excluded: set[str],
) -> tuple[list[dict[str, Any]], list[str]]:
    qmd = resolve_qmd(config, vault)
    if qmd is None:
        return [], ["auto-context skipped: qmd executable not found or not usable"]

    result = run_qmd(qmd, qmd_query_args(config, vault, idea))
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip() or f"exit code {result.returncode}"
        return [], [f"auto-context skipped: qmd query failed: {detail}"]

    try:
        rows = json.loads(result.stdout or "[]")
    except json.JSONDecodeError as exc:
        return [], [f"auto-context skipped: qmd returned invalid JSON: {exc}"]
    if not isinstance(rows, list):
        return [], ["auto-context skipped: qmd returned non-list JSON"]

    contexts: list[dict[str, Any]] = []
    seen: set[str] = set(excluded)
    collection = collection_name_for_vault(vault, config)
    for row in rows:
        if not isinstance(row, dict):
            continue
        rel = normalize_qmd_path(str(row.get("file") or ""), collection)
        page = pages.get(rel)
        if page is None or rel in seen:
            continue
        seen.add(rel)
        context = page_context(page, reason="auto")
        context["qmd_score"] = row.get("score")
        snippet = str(row.get("snippet") or row.get("body") or "").strip()
        if snippet:
            context["qmd_snippet"] = trim(snippet, 900)
        contexts.append(context)
        if len(contexts) >= MAX_AUTO_CONTEXT:
            break
    return contexts, []


def qmd_query_args(config: dict[str, str], vault: Path, idea: str) -> list[str]:
    return [
        "query",
        "--json",
        "--no-rerank",
        "-n",
        "10",
        "-c",
        collection_name_for_vault(vault, config),
        f"lex: {lex_query(idea)}\nvec: {vec_query(idea)}",
    ]


def lex_query(value: str) -> str:
    terms = re.findall(r"[A-Za-z0-9][A-Za-z0-9_-]*", value.lower())
    return " ".join(terms[:8]) or value


def vec_query(value: str) -> str:
    # QMD treats "-term" as lexical negation even inside vec/hyde lines.
    return re.sub(r"(?<=\w)-(?=\w)", " ", value).strip()


def normalize_qmd_path(value: str, collection: str) -> str:
    path = value
    if path.startswith("qmd://"):
        path = path[len("qmd://") :]
    collection_prefix = f"{collection}/"
    if path.startswith(collection_prefix):
        path = path[len(collection_prefix) :]
    path = path.lstrip("/")
    if not path.endswith(".md"):
        path = f"{path}.md"
    if path.split("/", 1)[0] not in WIKI_PAGE_DIRS:
        return ""
    return path


def page_context(page: Page, *, reason: str) -> dict[str, Any]:
    return {
        "path": page.rel,
        "title": page.title,
        "category": page.category,
        "tags": page.tags,
        "summary": page.summary,
        "reason": reason,
        "excerpt": trim(page.body, MAX_BODY_CHARS),
    }


def build_prompt(vault: Path, packet_path: Path, output_path: Path) -> str:
    return (
        "Use the `wiki-ideate` skill to write a durable idea packet from this deterministic context packet.\n\n"
        f"Vault path: {vault}\n"
        f"Skill file: {skill_path(vault)}\n"
        f"Deterministic packet path: {packet_path}\n"
        f"Write Markdown idea packet to: {output_path}\n\n"
        "Read the packet first. Write exactly one Markdown file at the requested output path. "
        "Do not update manifest, index, log, hot cache, QMD, or normal wiki pages."
    )


def skill_path(vault: Path) -> Path:
    local = vault / ".agents" / "skills" / "wiki-ideate" / "SKILL.md"
    if local.exists():
        return local
    legacy = vault / ".skills" / "wiki-ideate" / "SKILL.md"
    if legacy.exists():
        return legacy
    return Path(__file__).resolve().parents[1] / ".skills" / "wiki-ideate" / "SKILL.md"


def validate_output(path: Path) -> tuple[bool, str]:
    if not path.exists():
        return False, f"Codex did not write idea packet: {path}"
    fm = parse_frontmatter_file(path)
    tags = frontmatter_tags(path, fm)
    if A_INF_TAG not in tags:
        return False, f"idea packet must include tags: [{A_INF_TAG}]"
    return True, ""


def frontmatter_tags(path: Path, frontmatter: dict[str, Any] | None = None) -> list[str]:
    fm = frontmatter if frontmatter is not None else parse_frontmatter_file(path)
    raw = fm.get("tags") if isinstance(fm, dict) else None
    if isinstance(raw, str):
        tags = [raw.strip()]
    elif isinstance(raw, list):
        tags = [str(item).strip() for item in raw if str(item).strip()]
    else:
        tags = []
    if tags:
        return tags

    lines = read_optional(path).splitlines()
    if not lines or lines[0].strip() != "---":
        return []
    in_tags = False
    for line in lines[1:]:
        stripped = line.strip()
        if stripped == "---":
            break
        if stripped.startswith("tags:"):
            in_tags = True
            value = stripped[len("tags:") :].strip()
            if value.startswith("[") and value.endswith("]"):
                return [part.strip().strip('"').strip("'") for part in value[1:-1].split(",") if part.strip()]
            if value:
                return [value.strip('"').strip("'")]
            continue
        if in_tags and stripped.startswith("-"):
            tags.append(stripped.lstrip("-").strip().strip('"').strip("'"))
            continue
        if in_tags and stripped:
            break
    return tags


def build_report(packet: dict[str, Any], *, status: str, warnings: list[str] | None = None) -> dict[str, Any]:
    return {
        "version": 1,
        "status": status,
        "generated_at": now_iso(),
        "vault": packet.get("vault"),
        "output_path": packet.get("output_path"),
        "summary": {
            "explicit_context": len(packet.get("explicit_context", [])),
            "auto_context": len(packet.get("auto_context", [])),
            "warnings": len(warnings or []),
        },
        "warnings": warnings or [],
    }


def print_report(report: dict[str, Any], *, json_output: bool) -> None:
    if json_output:
        print(json.dumps(report, indent=2, sort_keys=True))
        return
    summary = report["summary"]
    lines = [
        "## Ideation Report",
        "",
        f"- **Status:** {report['status']}",
        f"- **Output:** {report.get('output_path') or '-'}",
        f"- **Explicit context:** {summary['explicit_context']}",
        f"- **Auto context:** {summary['auto_context']}",
    ]
    if report.get("warnings"):
        lines.extend(["", "### Warnings", ""])
        lines.extend(f"- {warning}" for warning in report["warnings"])
    print("\n".join(lines).rstrip() + "\n")


def create_run(vault: Path, idea: str) -> Run:
    output = unique_output_path(vault, idea)
    candidate = timestamped_run_dir(vault, "ideate")
    return Run(
        run_dir=candidate,
        packet_path=candidate / "packet.json",
        report_path=candidate / "report.json",
        output_path=output,
    )


def unique_output_path(vault: Path, idea: str) -> Path:
    ideas = vault / "ideas"
    ideas.mkdir(parents=True, exist_ok=True)
    slug = slugify(idea) or "idea"
    candidate = ideas / f"{slug}.md"
    suffix = 2
    while candidate.exists():
        candidate = ideas / f"{slug}-{suffix}.md"
        suffix += 1
    return candidate


def slugify(value: str) -> str:
    ascii_value = value.encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_value).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)
    return slug[:60].strip("-")


def strip_frontmatter(text: str) -> str:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return text
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return "\n".join(lines[index + 1 :]).strip()
    return text


def trim(value: str, max_chars: int) -> str:
    text = value.strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 15].rstrip() + "\n[... truncated]"


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
