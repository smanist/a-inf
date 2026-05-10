from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from a_inf.ingest import parse_datetime, parse_frontmatter_file
from a_inf.qmd import (
    QmdInfo,
    collection_name_for_vault,
    ensure_qmd_collection,
    qmd_env,
    resolve_qmd,
    run_qmd,
)


WIKI_PAGE_DIRS = {"concepts", "entities", "skills", "references", "synthesis", "journal", "projects"}
FILTERED_MODE_TRIGGERS = [
    "public only",
    "user-facing",
    "no internal content",
    "as a user would see it",
    "exclude internal",
]
INDEX_ONLY_TRIGGERS = ["quick answer", "just scan", "don't read the pages", "fast lookup"]
BLOCKED_VISIBILITY_TAGS = {"visibility/internal", "visibility/pii"}
WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:[#|][^\]]*)?\]\]")
SOURCE_DETAIL_TRIGGERS = {
    "algorithm",
    "derivation",
    "detail",
    "details",
    "equation",
    "equations",
    "exact",
    "formula",
    "formulas",
    "loss",
    "math",
    "notation",
    "objective",
    "original",
    "proof",
    "quote",
    "source",
}
SOURCE_DETAIL_MAX_CHARS = 200_000
SOURCE_DETAIL_SNIPPET_CHARS = 900


@dataclass(frozen=True)
class QueryModes:
    query_type: str
    index_only: bool
    filtered: bool


@dataclass(frozen=True)
class PageInfo:
    path: str
    title: str
    category: str
    tags: list[str] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)
    summary: str = ""
    updated: str = ""
    lifecycle: str = ""
    lifecycle_changed: str = ""
    lifecycle_reason: str = ""
    superseded_by: str = ""
    index_entry: str = ""

    @property
    def stem(self) -> str:
        return Path(self.path).stem


@dataclass(frozen=True)
class QmdResult:
    path: str
    score: float
    title: str
    snippet: str
    docid: str = ""


@dataclass
class Candidate:
    page: PageInfo
    score: float = 0.0
    reasons: list[str] = field(default_factory=list)
    snippets: list[str] = field(default_factory=list)
    qmd_score: float = 0.0


def run_query(args: Any, vault: Path, config: dict[str, str]) -> int:
    question = " ".join(getattr(args, "args", [])).strip()
    if not question:
        print("a-inf query requires a question.", file=sys.stderr)
        return 2

    if not ensure_qmd_collection(vault, config):
        return 127
    qmd = resolve_qmd(config, vault)
    if qmd is None:
        return 127

    packet = build_retrieval_packet(vault, config, question, qmd)
    prompt = build_query_prompt(vault, question, packet)

    if getattr(args, "print_prompt", False) or getattr(args, "no_codex", False):
        print(prompt)
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
    ]
    for directory in getattr(args, "add_dir", []) or []:
        command.extend(["--add-dir", str(Path(directory).expanduser().resolve())])
    command.append(prompt)
    return subprocess.call(command, cwd=vault, env=qmd_env(os.environ, vault))


def build_retrieval_packet(vault: Path, config: dict[str, str], question: str, qmd: QmdInfo) -> dict[str, Any]:
    modes = classify_query(question)
    index_text = read_text_if_exists(vault / "index.md")
    hot_text = read_text_if_exists(vault / "hot.md")
    pages = build_page_registry(vault, index_text)
    allowed_pages = {
        path: page
        for path, page in pages.items()
        if not modes.filtered or not (set(page.tags) & BLOCKED_VISIBILITY_TAGS)
    }

    qmd_results: list[QmdResult] = []
    warnings: list[str] = []
    if not modes.index_only:
        qmd_results, warnings = run_qmd_query(vault, config, qmd, question, allowed_pages)

    candidates = rank_candidates(question, allowed_pages, qmd_results)
    top_candidates = candidates[:10]
    graph_context = build_graph_context(vault, allowed_pages, top_candidates[:5])
    cited_paths = [candidate.page.path for candidate in top_candidates]
    source_details = build_source_detail_context(
        vault,
        config,
        question,
        allowed_pages,
        top_candidates,
        warnings,
    )

    return {
        "question": question,
        "mode": "filtered" if modes.filtered else "index_only" if modes.index_only else "normal",
        "query_type": modes.query_type,
        "filtered": modes.filtered,
        "index_only": modes.index_only,
        "qmd": {
            "required": True,
            "collection": collection_name_for_vault(vault, config),
            "command": None if modes.index_only else qmd_command_preview(config, vault, question),
            "warnings": warnings,
        },
        "hot": trim_text(hot_text, 1200),
        "index_summary": trim_text(index_text, 2200),
        "candidates": [candidate_to_packet(candidate, vault) for candidate in top_candidates],
        "source_details": source_details,
        "graph_context": graph_context,
        "lifecycle_annotations": {
            path: annotation
            for path in cited_paths
            if (annotation := lifecycle_annotation(allowed_pages[path]))
        },
        "gaps": gap_notes(question, top_candidates, warnings),
    }


def classify_query(question: str) -> QueryModes:
    lowered = question.lower()
    if any(term in lowered for term in ["relate", "relationship", "connect", "connection", "between"]):
        query_type = "relationship"
    elif any(term in lowered for term in ["what don't i know", "missing", "gap", "open question"]):
        query_type = "gap"
    elif any(term in lowered for term in ["current thinking", "synthesize", "everything related", "what do i know"]):
        query_type = "synthesis"
    else:
        query_type = "factual"
    return QueryModes(
        query_type=query_type,
        index_only=any(trigger in lowered for trigger in INDEX_ONLY_TRIGGERS),
        filtered=any(trigger in lowered for trigger in FILTERED_MODE_TRIGGERS),
    )


def source_detail_mode(config: dict[str, str]) -> str:
    mode = (os.environ.get("A_INF_QUERY_SOURCE_DETAIL") or config.get("A_INF_QUERY_SOURCE_DETAIL") or "auto").lower()
    return mode if mode in {"auto", "explicit", "always", "off"} else "auto"


def should_include_source_details(
    config: dict[str, str],
    question: str,
    candidates: list[Candidate],
    warnings: list[str],
) -> bool:
    mode = source_detail_mode(config)
    if mode == "off":
        return False
    if mode == "always":
        return True
    terms = set(query_terms(question))
    explicit = bool(terms & SOURCE_DETAIL_TRIGGERS)
    if mode == "explicit":
        return explicit
    if explicit or warnings or not candidates:
        return True
    return bool(candidates and candidates[0].score < 15)


def build_page_registry(vault: Path, index_text: str = "") -> dict[str, PageInfo]:
    index_entries = parse_index_entries(index_text)
    pages: dict[str, PageInfo] = {}
    for category in sorted(WIKI_PAGE_DIRS):
        root = vault / category
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.md")):
            if not path.is_file():
                continue
            rel = path.relative_to(vault).as_posix()
            fm = parse_frontmatter_file(path)
            title = str(fm.get("title") or Path(rel).stem)
            tags = as_text_list(fm.get("tags")) or read_frontmatter_sequence(path, "tags")
            pages[rel] = PageInfo(
                path=rel,
                title=title,
                category=str(fm.get("category") or rel.split("/", 1)[0]),
                tags=tags,
                aliases=as_text_list(fm.get("aliases")) or read_frontmatter_sequence(path, "aliases"),
                summary=str(fm.get("summary") or ""),
                updated=str(fm.get("updated") or ""),
                lifecycle=str(fm.get("lifecycle") or ""),
                lifecycle_changed=str(fm.get("lifecycle_changed") or ""),
                lifecycle_reason=str(fm.get("lifecycle_reason") or ""),
                superseded_by=str(fm.get("superseded_by") or ""),
                index_entry=index_entries.get(rel) or index_entries.get(rel.removesuffix(".md")) or "",
            )
    return pages


def parse_index_entries(index_text: str) -> dict[str, str]:
    entries: dict[str, str] = {}
    for line in index_text.splitlines():
        if ".md" not in line and "[[" not in line:
            continue
        for match in re.finditer(r"\(([^)]+\.md)\)|\[\[([^\]|]+)(?:\|[^\]]+)?\]\]", line):
            target = (match.group(1) or match.group(2) or "").lstrip("./")
            if target and not target.startswith(("http://", "https://")):
                if not target.endswith(".md"):
                    target = f"{target}.md"
                entries[target] = line.strip()
    return entries


def run_qmd_query(
    vault: Path,
    config: dict[str, str],
    qmd: QmdInfo,
    question: str,
    allowed_pages: dict[str, PageInfo],
) -> tuple[list[QmdResult], list[str]]:
    result = run_qmd(qmd, qmd_query_args(config, vault, question))
    if result.returncode != 0:
        message = (result.stderr or result.stdout).strip()
        return [], [f"QMD query failed: {message or f'exit code {result.returncode}'}"]
    try:
        rows = json.loads(result.stdout or "[]")
    except json.JSONDecodeError as exc:
        return [], [f"QMD returned invalid JSON: {exc}"]
    qmd_results: list[QmdResult] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        rel = normalize_qmd_path(str(row.get("file") or ""), collection_name_for_vault(vault, config))
        if rel not in allowed_pages:
            continue
        qmd_results.append(
            QmdResult(
                path=rel,
                score=float(row.get("score") or 0.0),
                title=str(row.get("title") or ""),
                snippet=str(row.get("snippet") or row.get("body") or ""),
                docid=str(row.get("docid") or ""),
            )
        )
    return qmd_results, []


def qmd_query_args(config: dict[str, str], vault: Path, question: str) -> list[str]:
    return [
        "query",
        "--json",
        "--no-rerank",
        "-n",
        "10",
        "-c",
        collection_name_for_vault(vault, config),
        f"lex: {lex_query(question)}\nvec: {question}",
    ]


def qmd_command_preview(config: dict[str, str], vault: Path, question: str) -> list[str]:
    return ["qmd", *qmd_query_args(config, vault, question)]


def lex_query(question: str) -> str:
    terms = query_terms(question)
    return " ".join(terms[:8]) or question


def normalize_qmd_path(value: str, collection: str) -> str:
    path = value
    prefix = "qmd://"
    if path.startswith(prefix):
        path = path[len(prefix) :]
    collection_prefix = f"{collection}/"
    if path.startswith(collection_prefix):
        path = path[len(collection_prefix) :]
    path = path.lstrip("/")
    if not path.endswith(".md"):
        path = f"{path}.md"
    if path.split("/", 1)[0] not in WIKI_PAGE_DIRS:
        return ""
    return path


def rank_candidates(question: str, pages: dict[str, PageInfo], qmd_results: list[QmdResult]) -> list[Candidate]:
    terms = query_terms(question)
    candidates = {path: Candidate(page=page) for path, page in pages.items()}
    for result in qmd_results:
        candidate = candidates.get(result.path)
        if candidate is None:
            continue
        candidate.qmd_score = result.score
        candidate.score += result.score * 100
        candidate.reasons.append(f"qmd:{result.score:.2f}")
        if result.snippet:
            candidate.snippets.append(trim_text(result.snippet, 900))

    for candidate in candidates.values():
        bonus_reasons = score_frontmatter_match(candidate.page, terms)
        for reason, points in bonus_reasons:
            candidate.score += points
            candidate.reasons.append(reason)

    ranked = [candidate for candidate in candidates.values() if candidate.score > 0]
    ranked.sort(key=lambda candidate: (-candidate.score, candidate.page.path))
    return ranked


def score_frontmatter_match(page: PageInfo, terms: list[str]) -> list[tuple[str, float]]:
    if not terms:
        return []
    reasons: list[tuple[str, float]] = []
    title = normalize_text(page.title)
    stem = normalize_text(page.stem)
    aliases = [normalize_text(alias) for alias in page.aliases]
    tags = [normalize_text(tag) for tag in page.tags]
    summary = normalize_text(page.summary)
    index_entry = normalize_text(page.index_entry)
    term_set = set(terms)
    phrase = " ".join(terms)

    if (
        title in term_set
        or stem in term_set
        or title in phrase
        or stem in phrase
        or any(alias in term_set or alias in phrase for alias in aliases)
    ):
        reasons.append(("exact-title-alias-or-stem", 40.0))
    if any(term in tags for term in terms):
        reasons.append(("tag-match", 20.0))
    if any(term in summary for term in terms):
        reasons.append(("summary-match", 10.0))
    if any(term in index_entry for term in terms):
        reasons.append(("index-entry-match", 6.0))
    return reasons


def build_graph_context(
    vault: Path,
    pages: dict[str, PageInfo],
    candidates: list[Candidate],
) -> list[dict[str, Any]]:
    by_stem: dict[str, str] = {}
    for path, page in pages.items():
        by_stem.setdefault(page.stem.lower(), path)
        by_stem.setdefault(path.removesuffix(".md").lower(), path)

    context: list[dict[str, Any]] = []
    for candidate in candidates:
        page_path = vault / candidate.page.path
        links = resolve_outgoing_links(read_text_if_exists(page_path), pages, by_stem, candidate.page.path)
        context.append(
            {
                "page": candidate.page.path,
                "outgoing_links": [
                    {
                        "path": link,
                        "title": pages[link].title,
                        "tags": pages[link].tags,
                    }
                    for link in links[:12]
                ],
            }
        )
    return context


def build_source_detail_context(
    vault: Path,
    config: dict[str, str],
    question: str,
    allowed_pages: dict[str, PageInfo],
    candidates: list[Candidate],
    warnings: list[str],
) -> list[dict[str, Any]]:
    if not should_include_source_details(config, question, candidates, warnings):
        return []
    manifest = read_manifest(vault)
    sources = manifest.get("sources", {})
    if not isinstance(sources, dict):
        return []
    candidate_paths = {candidate.page.path for candidate in candidates[:5]}
    terms = query_terms(question)
    details: list[dict[str, Any]] = []
    for manifest_key, raw_entry in sources.items():
        if not isinstance(raw_entry, dict):
            continue
        pages = source_entry_pages(raw_entry)
        allowed_entry_pages = [page for page in pages if page in allowed_pages]
        if not allowed_entry_pages:
            continue
        if candidate_paths and not (candidate_paths & set(allowed_entry_pages)) and source_detail_mode(config) != "always":
            if not (set(terms) & SOURCE_DETAIL_TRIGGERS):
                continue
        extracted = raw_entry.get("extracted_path")
        if not isinstance(extracted, str) or not extracted:
            continue
        path = resolve_vault_path(vault, extracted)
        text = read_text_if_exists(path)[:SOURCE_DETAIL_MAX_CHARS]
        if not text.strip():
            continue
        text_terms = [term for term in terms if term not in SOURCE_DETAIL_TRIGGERS]
        if candidate_paths and not (candidate_paths & set(allowed_entry_pages)):
            if not text_terms:
                continue
            normalized_text = normalize_text(text)
            if not any(term in normalized_text for term in text_terms):
                continue
        snippets = source_detail_snippets(text, terms)
        score = source_detail_score(text, terms, allowed_entry_pages, candidate_paths)
        if score <= 0 and source_detail_mode(config) != "always":
            continue
        details.append(
            {
                "manifest_key": str(manifest_key),
                "source_type": str(raw_entry.get("source_type") or ""),
                "source_url": str(raw_entry.get("source_url") or ""),
                "archive_id": str(raw_entry.get("archive_id") or ""),
                "extracted_path": extracted,
                "original_path": str(raw_entry.get("original_path") or ""),
                "reference_pages": [page for page in allowed_entry_pages if page.startswith("references/")],
                "pages": allowed_entry_pages,
                "score": score,
                "snippets": snippets,
            }
        )
    details.sort(key=lambda item: (-float(item["score"]), str(item["manifest_key"])))
    return details[:5]


def source_entry_pages(entry: dict[str, Any]) -> list[str]:
    pages: list[str] = []
    for key in ["pages_created", "pages_updated"]:
        value = entry.get(key)
        if isinstance(value, list):
            pages.extend(str(item) for item in value if str(item))
    return list(dict.fromkeys(pages))


def resolve_vault_path(vault: Path, value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else vault / path


def source_detail_score(text: str, terms: list[str], pages: list[str], candidate_paths: set[str]) -> float:
    normalized = normalize_text(text)
    score = 4.0 if candidate_paths & set(pages) else 1.0
    for term in terms:
        if term in SOURCE_DETAIL_TRIGGERS:
            score += 2.0
            continue
        score += min(normalized.count(term), 5)
    return score


def source_detail_snippets(text: str, terms: list[str]) -> list[str]:
    normalized = normalize_text(text)
    snippets: list[str] = []
    for term in terms:
        if term in SOURCE_DETAIL_TRIGGERS:
            continue
        index = normalized.find(term)
        if index < 0:
            continue
        start = max(0, index - SOURCE_DETAIL_SNIPPET_CHARS // 2)
        end = min(len(text), index + SOURCE_DETAIL_SNIPPET_CHARS // 2)
        snippets.append(trim_text(text[start:end], SOURCE_DETAIL_SNIPPET_CHARS))
        if len(snippets) >= 2:
            break
    if not snippets:
        snippets.append(trim_text(text, SOURCE_DETAIL_SNIPPET_CHARS))
    return snippets


def resolve_outgoing_links(
    text: str,
    pages: dict[str, PageInfo],
    by_stem: dict[str, str],
    source: str,
) -> list[str]:
    resolved: list[str] = []
    seen: set[str] = set()
    for match in WIKILINK_RE.finditer(text):
        target = match.group(1).strip()
        if not target:
            continue
        candidates = []
        normalized = target.removesuffix(".md")
        candidates.append(f"{normalized}.md")
        candidates.append(by_stem.get(normalized.lower(), ""))
        candidates.append(by_stem.get(Path(normalized).stem.lower(), ""))
        for candidate in candidates:
            if candidate and candidate in pages and candidate != source and candidate not in seen:
                seen.add(candidate)
                resolved.append(candidate)
                break
    return resolved


def candidate_to_packet(candidate: Candidate, vault: Path) -> dict[str, Any]:
    page = candidate.page
    snippets = candidate.snippets or local_snippets(vault / page.path, page, limit=2)
    return {
        "path": page.path,
        "title": page.title,
        "category": page.category,
        "tags": page.tags,
        "aliases": page.aliases,
        "summary": page.summary,
        "updated": page.updated,
        "lifecycle": page.lifecycle,
        "score": round(candidate.score, 3),
        "qmd_score": round(candidate.qmd_score, 3),
        "reasons": candidate.reasons,
        "snippets": snippets,
        "index_entry": page.index_entry,
    }


def local_snippets(path: Path, page: PageInfo, limit: int = 2) -> list[str]:
    snippets = [page.summary] if page.summary else []
    text = read_text_if_exists(path)
    if "## Open Questions" in text:
        snippets.append(trim_text(text.split("## Open Questions", 1)[1], 700))
    return [snippet for snippet in snippets if snippet][:limit]


def lifecycle_annotation(page: PageInfo) -> str:
    lifecycle = page.lifecycle or "draft"
    if lifecycle == "archived":
        target = f": superseded by {page.superseded_by}" if page.superseded_by else ""
        return f"ARCHIVED{target}"
    if lifecycle == "disputed":
        changed = f", marked {page.lifecycle_changed}" if page.lifecycle_changed else ""
        reason = f": {page.lifecycle_reason}" if page.lifecycle_reason else ": reason unspecified"
        return f"DISPUTED{changed}{reason}"
    if is_stale(page.updated):
        prefix = "VERIFIED but stale" if lifecycle == "verified" else "stale"
        return f"{prefix}: last updated {page.updated}"
    return ""


def is_stale(updated: str) -> bool:
    parsed = parse_datetime(updated)
    if parsed is None:
        return False
    return (datetime.now(timezone.utc) - parsed).days > 90


def build_query_prompt(vault: Path, question: str, packet: dict[str, Any]) -> str:
    packet_json = json.dumps(packet, indent=2, sort_keys=True)
    return (
        "Use the `wiki-query` skill to synthesize an answer from the deterministic retrieval packet below.\n\n"
        f"Vault/repo path: {vault}\n"
        f"Question: {question}\n\n"
        "Rules:\n"
        "- Do not redo broad retrieval or search the vault unless `qmd.warnings` says retrieval failed or the packet has no candidates.\n"
        "- Cite only pages present in `candidates` or `graph_context`.\n"
        "- Use `source_details` only for exact source evidence, quotes, equations, notation, derivations, or weak wiki coverage; cite the linked wiki/reference page first when available.\n"
        "- Apply `lifecycle_annotations` inline for cited pages when present.\n"
        "- If `filtered` is true, do not mention excluded pages or internal content.\n"
        "- Mention gaps from the packet when relevant.\n"
        "- Append the standard QUERY line to log.md after answering.\n\n"
        "Retrieval packet:\n"
        "```json\n"
        f"{packet_json}\n"
        "```\n"
    )


def gap_notes(question: str, candidates: list[Candidate], warnings: list[str]) -> list[str]:
    notes = list(warnings)
    if not candidates:
        notes.append("No candidate wiki pages matched the question.")
    elif classify_query(question).query_type == "gap":
        notes.append("Check candidate snippets and graph context for Open Questions sections before answering.")
    return notes


def query_terms(question: str) -> list[str]:
    stopwords = {
        "a",
        "about",
        "an",
        "and",
        "are",
        "as",
        "do",
        "does",
        "for",
        "how",
        "i",
        "in",
        "is",
        "it",
        "know",
        "my",
        "of",
        "on",
        "or",
        "the",
        "to",
        "what",
        "with",
    }
    terms = []
    for term in re.findall(r"[A-Za-z0-9][A-Za-z0-9_-]*", normalize_text(question)):
        if len(term) > 1 and term not in stopwords and term not in terms:
            terms.append(term)
    return terms


def normalize_text(value: str) -> str:
    return value.lower().replace("_", "-").strip()


def as_text_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def read_frontmatter_sequence(path: Path, key: str) -> list[str]:
    lines = read_text_if_exists(path).splitlines()
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
                try:
                    parsed = json.loads(inline)
                except json.JSONDecodeError:
                    parsed = []
                return [str(item) for item in parsed if str(item)] if isinstance(parsed, list) else []
            continue
        if in_key and (line.startswith(" ") or line.startswith("-")):
            value = stripped.lstrip("-").strip().strip('"').strip("'")
            if value:
                values.append(value)
            continue
        if in_key:
            break
    return values


def read_text_if_exists(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8") if path.exists() else ""
    except UnicodeDecodeError:
        return ""


def read_manifest(vault: Path) -> dict[str, Any]:
    path = vault / ".manifest.json"
    if not path.exists():
        return {"version": 1, "sources": {}, "projects": {}, "stats": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"version": 1, "sources": {}, "projects": {}, "stats": {}}
    if not isinstance(data, dict):
        return {"version": 1, "sources": {}, "projects": {}, "stats": {}}
    data.setdefault("sources", {})
    data.setdefault("projects", {})
    data.setdefault("stats", {})
    return data


def trim_text(value: str, max_chars: int) -> str:
    text = value.strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 15].rstrip() + "\n[... truncated]"
