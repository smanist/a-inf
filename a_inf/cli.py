from __future__ import annotations

import argparse
from collections.abc import Callable
import hashlib
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import tomllib
from urllib.parse import urlparse

from a_inf.qmd import ensure_qmd_collection, ensure_qmd_state_dirs, qmd_env, qmd_state_dirs, sync_qmd


VAULT_DIRS = [
    "concepts",
    "entities",
    "skills",
    "references",
    "synthesis",
    "journal",
    "projects",
    "_archives",
    "_raw",
    "_meta",
    ".obsidian",
    ".skills",
]

SKILL_ALIASES = {
    "ingest": "wiki-ingest",
    "ingest-url": "ingest-url",
    "data-ingest": "data-ingest",
    "query": "wiki-query",
    "update": "wiki-update",
    "history": "codex-history-ingest",
    "insights": "wiki-insights",
    "lint": "wiki-lint",
    "rebuild": "wiki-rebuild",
    "export": "wiki-export",
    "research": "wiki-research",
    "capture": "wiki-capture",
    "synthesize": "wiki-synthesize",
    "dashboard": "wiki-dashboard",
    "colorize": "graph-colorize",
    "cross-link": "cross-linker",
    "tags": "tag-taxonomy",
}

QMD_SYNC_SKILLS = {
    "wiki-ingest",
    "ingest-url",
    "data-ingest",
    "wiki-update",
    "codex-history-ingest",
    "wiki-history-ingest",
    "wiki-rebuild",
    "wiki-research",
    "wiki-capture",
    "wiki-synthesize",
    "wiki-dashboard",
    "graph-colorize",
    "cross-linker",
    "tag-taxonomy",
}


@dataclass(frozen=True)
class Dispatch:
    skill: str
    prompt: str


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="a-inf")
    parser.add_argument("--version", action="version", version="a-inf 0.1.0")

    sub = parser.add_subparsers(dest="command", required=True)

    init_parser = sub.add_parser("init", help="Initialize the current repo as an a-inf vault.")
    init_parser.add_argument("path", nargs="?", default=".", help="Vault/repo path to initialize.")
    init_parser.add_argument(
        "--skills-source",
        type=Path,
        default=None,
        help="Directory containing bundled skill folders. Defaults to this package's .skills directory.",
    )
    init_parser.add_argument(
        "--copy-skills",
        action="store_true",
        help="Copy skill directories instead of symlinking them.",
    )
    init_parser.add_argument(
        "--no-agents",
        action="store_true",
        help="Do not create or update AGENTS.md with local skill routing.",
    )
    init_parser.add_argument(
        "--no-gitignore",
        action="store_true",
        help="Do not add local a-inf config ignores to .gitignore.",
    )
    init_parser.add_argument(
        "--write-global-config",
        action="store_true",
        help="Write ~/.obsidian-wiki/config pointing to this vault and CLI repo.",
    )
    init_parser.set_defaults(func=cmd_init)

    status_parser = sub.add_parser("status", help="Show ingest state and deltas.")
    status_parser.add_argument(
        "--insights",
        action="store_true",
        help="Run the wiki-insights workflow instead of the deterministic status report.",
    )
    status_parser.add_argument(
        "args",
        nargs="*",
        help="Optional compatibility arguments. Insight-related words route to wiki-insights.",
    )
    add_dispatch_options(status_parser)
    status_parser.set_defaults(func=cmd_status)

    for name in [
        "ingest",
        "query",
        "update",
        "history",
        "insights",
        "lint",
        "rebuild",
        "export",
        "research",
        "capture",
        "synthesize",
        "dashboard",
        "colorize",
        "cross-link",
        "tags",
    ]:
        cmd = sub.add_parser(name, help=f"Run the {SKILL_ALIASES[name]} workflow.")
        if name == "ingest":
            cmd.add_argument(
                "--data",
                action="store_true",
                help="Route ingest through data-ingest for exports, logs, and transcripts.",
            )
            cmd.add_argument(
                "--mode",
                choices=["append", "full", "raw"],
                default="append",
                help="Ingest mode for deterministic wiki-ingest. Default: append.",
            )
            cmd.add_argument(
                "--full",
                action="store_true",
                help="Alias for --mode full.",
            )
            cmd.add_argument(
                "--raw",
                action="store_true",
                help="Alias for --mode raw.",
            )
        cmd.add_argument("args", nargs="*", help="Arguments passed to the workflow.")
        add_dispatch_options(cmd)
        cmd.set_defaults(func=cmd_dispatch, alias=name)

    skill_parser = sub.add_parser("skill", help="Run an arbitrary bundled skill by name.")
    skill_parser.add_argument("skill", help="Skill name, e.g. wiki-ingest.")
    skill_parser.add_argument("args", nargs="*", help="Arguments passed to the skill.")
    add_dispatch_options(skill_parser)
    skill_parser.set_defaults(func=cmd_skill)

    return parser


def add_dispatch_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--print-prompt",
        action="store_true",
        help="Print the generated Codex prompt instead of invoking Codex.",
    )
    parser.add_argument(
        "--no-codex",
        action="store_true",
        help="Do not invoke Codex; print the generated prompt.",
    )
    parser.add_argument(
        "--codex-bin",
        default="codex",
        help="Codex executable to invoke. Default: codex.",
    )
    parser.add_argument(
        "--sandbox",
        choices=["read-only", "workspace-write", "danger-full-access"],
        default="workspace-write",
        help="Sandbox mode for Codex-dispatched workflows. Default: workspace-write.",
    )
    parser.add_argument(
        "--add-dir",
        action="append",
        default=[],
        help="Additional directory Codex may read/write. Repeat for multiple directories.",
    )


def cmd_init(args: argparse.Namespace) -> int:
    vault = Path(args.path).expanduser().resolve()
    skills_source = resolve_skills_source(args.skills_source)

    vault.mkdir(parents=True, exist_ok=True)
    for dirname in VAULT_DIRS:
        (vault / dirname).mkdir(parents=True, exist_ok=True)

    write_file_if_missing(vault / "index.md", index_template())
    write_file_if_missing(vault / "log.md", log_template(vault))
    write_file_if_missing(vault / "hot.md", hot_template(vault))
    write_file_if_missing(vault / "_meta" / "taxonomy.md", taxonomy_template())
    write_json_if_missing(vault / ".manifest.json", manifest_template())
    write_json_if_missing(
        vault / ".obsidian" / "app.json",
        {
            "strictLineBreaks": False,
            "showFrontmatter": False,
            "defaultViewMode": "preview",
            "livePreview": True,
        },
    )
    write_json_if_missing(vault / ".obsidian" / "appearance.json", {"baseFontSize": 16})
    write_local_config(vault, skills_source)
    write_file_if_missing(vault / ".env", env_template(vault))

    linked = install_skills(skills_source, vault / ".skills", copy=args.copy_skills)

    if not args.no_agents:
        ensure_agents_section(vault / "AGENTS.md")
    if not args.no_gitignore:
        ensure_gitignore_section(vault / ".gitignore")

    if args.write_global_config:
        write_global_config(vault)

    config = load_wiki_config(vault)
    for key in ["QMD_WIKI_COLLECTION", "QMD_PAPERS_COLLECTION"]:
        local_value = read_env_value(vault / ".env", key)
        if local_value:
            config[key] = local_value
        else:
            config.setdefault(key, vault.name)
    if not ensure_qmd_collection(vault, config):
        return 127

    print(f"Initialized a-inf vault: {vault}")
    print(f"Skills source: {skills_source}")
    print(f"Skills installed locally: {linked}")
    print("Next: a-inf ingest <source> or a-inf status")
    return 0


def cmd_dispatch(args: argparse.Namespace) -> int:
    alias = args.alias
    if alias == "ingest":
        skill = infer_ingest_skill(args.args, data=getattr(args, "data", False))
        if skill == "wiki-ingest":
            from a_inf.ingest import run_hybrid_ingest

            return run_hybrid_ingest(args, find_vault_root(Path.cwd()))
    else:
        skill = SKILL_ALIASES[alias]
    dispatch = build_dispatch(skill, args.args)
    return run_dispatch(dispatch, args)


def cmd_skill(args: argparse.Namespace) -> int:
    dispatch = build_dispatch(args.skill, args.args)
    return run_dispatch(dispatch, args)


def cmd_status(args: argparse.Namespace) -> int:
    insight_terms = " ".join(getattr(args, "args", [])).lower()
    if getattr(args, "insights", False) or any(
        term in insight_terms
        for term in ["insight", "hubs", "hub", "central", "structure", "connected", "bridge"]
    ):
        dispatch = build_dispatch("wiki-insights", getattr(args, "args", []))
        return run_dispatch(dispatch, args)

    vault = find_vault_root(Path.cwd())
    print(build_status_report(vault))
    return 0


def run_dispatch(dispatch: Dispatch, args: argparse.Namespace) -> int:
    if args.print_prompt or args.no_codex:
        print(dispatch.prompt)
        return 0

    codex_bin = shutil.which(args.codex_bin)
    if codex_bin is None:
        print("Codex executable not found. Re-run with --print-prompt or install Codex CLI.", file=sys.stderr)
        print(dispatch.prompt)
        return 127

    vault = find_vault_root(Path.cwd())
    command = [codex_bin, "exec", "--sandbox", args.sandbox, "--cd", str(vault)]
    add_dirs = [*default_add_dirs(vault, dispatch.skill), *args.add_dir]
    if dispatch.skill in QMD_SYNC_SKILLS:
        ensure_qmd_state_dirs(vault)
        add_dirs.extend(directory for directory in qmd_state_dirs(vault) if directory.exists())
    seen_dirs: set[Path] = set()
    for directory in add_dirs:
        resolved = Path(directory).expanduser().resolve()
        if resolved in seen_dirs:
            continue
        seen_dirs.add(resolved)
        command.extend(["--add-dir", str(resolved)])
    command.append(dispatch.prompt)
    if dispatch.skill in QMD_SYNC_SKILLS and not ensure_qmd_collection(vault, load_wiki_config(vault)):
        return 127
    result = subprocess.call(command, cwd=vault, env=qmd_env(os.environ, vault))
    if result == 0 and dispatch.skill in QMD_SYNC_SKILLS and args.sandbox != "read-only":
        config = load_wiki_config(vault)
        if not sync_qmd(vault, config):
            print("warning: QMD sync failed after workflow; vault files may still have been updated.", file=sys.stderr)
    return result


def build_dispatch(skill: str, workflow_args: list[str]) -> Dispatch:
    vault = find_vault_root(Path.cwd())
    skill_path = vault / ".skills" / skill / "SKILL.md"
    args_text = " ".join(workflow_args).strip()
    if not skill_path.exists():
        skill_path = resolve_skills_source(None) / skill / "SKILL.md"

    prompt = (
        f"Use the `{skill}` skill to operate on this a-inf vault.\n\n"
        f"Vault/repo path: {vault}\n"
        f"Skill file: {skill_path}\n"
        f"CLI arguments: {args_text or '(none)'}\n\n"
        "Follow the skill instructions exactly. Resolve configuration from `.a-inf/config.toml`, "
        "`~/.obsidian-wiki/config`, or `.env` as applicable. Update manifest, index, log, and hot cache "
        "only when the selected workflow requires those updates."
    )
    return Dispatch(skill=skill, prompt=prompt)


def default_add_dirs(vault: Path, skill: str) -> list[Path]:
    if skill not in {"codex-history-ingest", "wiki-history-ingest"}:
        return []

    history_path = os.environ.get("CODEX_HISTORY_PATH") or read_env_value(
        vault / ".env", "CODEX_HISTORY_PATH"
    )
    path = Path(history_path).expanduser() if history_path else Path.home() / ".codex"
    return [path] if path.exists() else []


def read_env_value(path: Path, key: str) -> str | None:
    if not path.exists():
        return None
    prefix = f"{key}="
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or not stripped.startswith(prefix):
            continue
        value = stripped[len(prefix) :].strip().strip('"').strip("'")
        return value or None
    return None


@dataclass(frozen=True)
class SourceFile:
    path: Path
    display: str
    size_bytes: int
    modified_at: datetime
    source_type: str


@dataclass(frozen=True)
class SourceDelta:
    source: SourceFile | None
    manifest_key: str
    status: str
    reason: str
    entry: dict[str, object]


WIKI_PAGE_DIRS = ["concepts", "entities", "skills", "references", "synthesis", "journal", "projects"]
TEXT_SUFFIXES = {
    ".bash",
    ".c",
    ".cpp",
    ".css",
    ".csv",
    ".go",
    ".h",
    ".hpp",
    ".htm",
    ".html",
    ".java",
    ".js",
    ".json",
    ".jsonl",
    ".jsx",
    ".log",
    ".markdown",
    ".md",
    ".org",
    ".py",
    ".rs",
    ".rst",
    ".scss",
    ".sh",
    ".sql",
    ".toml",
    ".ts",
    ".tsv",
    ".tsx",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
    ".zsh",
}


def build_status_report(vault: Path) -> str:
    config = load_wiki_config(vault)
    manifest, manifest_exists = read_manifest(vault)
    sources = scan_sources(vault, config, manifest)
    deltas = classify_sources(sources, manifest)
    page_counts, visibility = scan_wiki_pages(vault)

    source_entries = manifest.get("sources", {})
    projects = manifest.get("projects", {})
    stats = manifest.get("stats", {})
    total_ingested = int(stats.get("total_sources_ingested") or len(source_entries))
    total_projects = int(stats.get("total_projects") or len(projects))
    last_ingest = latest_ingest_time(manifest)

    new = [delta for delta in deltas if delta.status == "new"]
    modified = [delta for delta in deltas if delta.status == "modified"]
    touched = [delta for delta in deltas if delta.status == "touched"]
    unchanged = [delta for delta in deltas if delta.status == "unchanged"]
    deleted = [delta for delta in deltas if delta.status == "deleted"]

    ready_count = len(new) + len(modified)
    recommendation = recommend_status_action(
        manifest_exists=manifest_exists,
        ingested_count=len(source_entries),
        ready_count=ready_count,
        deleted_count=len(deleted),
    )

    category_count = sum(1 for count in page_counts.values() if count)
    total_pages = sum(page_counts.values())
    lines = [
        "# Wiki Status",
        "",
        "## Overview",
        f"- **Vault:** {vault}",
        f"- **Total wiki pages:** {total_pages} across {category_count} categories",
    ]
    if visibility["internal"] or visibility["pii"] or visibility["explicit_public"]:
        lines.append(
            f"- **Page visibility:** {visibility['public']} public, "
            f"{visibility['internal']} internal, {visibility['pii']} pii"
        )
    lines.extend(
        [
            f"- **Total sources ingested:** {total_ingested}",
            f"- **Projects tracked:** {total_projects}",
            f"- **Last ingest:** {last_ingest or 'never'}",
            f"- **Configured document sources:** {format_configured_paths(config.get('OBSIDIAN_SOURCES_DIR'))}",
            f"- **Codex history path:** "
            f"{format_configured_paths(os.environ.get('CODEX_HISTORY_PATH') or config.get('CODEX_HISTORY_PATH'))}",
            "",
            "## Delta (what's changed since last ingest)",
            "",
            f"### New sources (never ingested): {len(new)}",
            render_delta_table(new, ["Source", "Type", "Size"], lambda d: [
                d.source.display if d.source else d.manifest_key,
                d.source.source_type if d.source else source_type_from_entry(d.entry),
                format_bytes(d.source.size_bytes) if d.source else "-",
            ]),
            "",
            f"### Modified sources (need re-ingesting): {len(modified)}",
            render_delta_table(modified, ["Source", "Last ingested", "Last modified", "Delta"], lambda d: [
                d.source.display if d.source else d.manifest_key,
                str(d.entry.get("ingested_at") or "-"),
                format_datetime(d.source.modified_at) if d.source else "-",
                d.reason,
            ]),
            "",
            f"### Touched sources (content unchanged): {len(touched)}",
            render_delta_table(touched, ["Source", "Reason"], lambda d: [
                d.source.display if d.source else d.manifest_key,
                d.reason,
            ]),
            "",
            f"### Deleted sources (ingested but gone): {len(deleted)}",
            render_delta_table(deleted, ["Source", "Last ingested"], lambda d: [
                d.manifest_key,
                str(d.entry.get("ingested_at") or "-"),
            ]),
            "",
            "## Summary",
            f"- **Ready to ingest:** {len(new)} new + {len(modified)} modified = {ready_count} sources",
            f"- **Up to date:** {len(unchanged)} unchanged",
            f"- **Touched but identical:** {len(touched)}",
            f"- **Deleted:** {len(deleted)}",
            f"- **Recommendation:** {recommendation}",
        ]
    )
    return "\n".join(lines)


def load_wiki_config(vault: Path) -> dict[str, str]:
    config: dict[str, str] = {}
    local_config = vault / ".a-inf" / "config.toml"
    if local_config.exists():
        data = tomllib.loads(local_config.read_text(encoding="utf-8"))
        for key, value in data.items():
            config[str(key)] = str(value)

    global_config = Path.home() / ".obsidian-wiki" / "config"
    if global_config.exists():
        config.update({k: v for k, v in read_env_file(global_config).items() if k not in config})

    env_config = vault / ".env"
    if env_config.exists():
        config.update({k: v for k, v in read_env_file(env_config).items() if k not in config})

    if "vault_path" in config and "OBSIDIAN_VAULT_PATH" not in config:
        config["OBSIDIAN_VAULT_PATH"] = config["vault_path"]
    return config


def read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("export "):
            stripped = stripped[len("export ") :].strip()
        if "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def read_manifest(vault: Path) -> tuple[dict[str, object], bool]:
    path = vault / ".manifest.json"
    if not path.exists():
        return {"version": 1, "sources": {}, "projects": {}, "stats": {}}, False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"version": 1, "sources": {}, "projects": {}, "stats": {}}, False
    if not isinstance(data, dict):
        return {"version": 1, "sources": {}, "projects": {}, "stats": {}}, False
    data.setdefault("sources", {})
    data.setdefault("projects", {})
    data.setdefault("stats", {})
    return data, True


def scan_sources(vault: Path, config: dict[str, str], manifest: dict[str, object]) -> dict[str, SourceFile]:
    sources: dict[str, SourceFile] = {}
    for directory in split_config_paths(config.get("OBSIDIAN_SOURCES_DIR")):
        if not directory.exists() or not directory.is_dir():
            continue
        for path in directory.rglob("*"):
            if path.is_file() and is_text_source(path):
                add_source_file(sources, path, "document")

    history_path = configured_history_path(config)
    if history_path and history_path.exists():
        for relative_pattern, source_type in [
            ("session_index.jsonl", "codex_index"),
            ("history.jsonl", "codex_history"),
            ("sessions/**/rollout-*.jsonl", "codex_rollout"),
            ("archived_sessions/**/rollout-*.jsonl", "codex_rollout_archived"),
        ]:
            for path in history_path.glob(relative_pattern):
                if path.is_file():
                    add_source_file(sources, path, source_type)

    manifest_sources = manifest.get("sources", {})
    if isinstance(manifest_sources, dict):
        for key, entry in manifest_sources.items():
            if not is_path_source(key):
                continue
            path = Path(key).expanduser()
            if path.exists() and path.is_file():
                entry_type = source_type_from_entry(entry if isinstance(entry, dict) else {})
                add_source_file(sources, path, entry_type)
    return sources


def split_config_paths(raw: str | None) -> list[Path]:
    if not raw:
        return []
    normalized = raw.replace(",", os.pathsep)
    paths = []
    for value in normalized.split(os.pathsep):
        stripped = value.strip()
        if stripped:
            paths.append(Path(stripped).expanduser())
    return paths


def configured_history_path(config: dict[str, str]) -> Path | None:
    raw = os.environ.get("CODEX_HISTORY_PATH") or config.get("CODEX_HISTORY_PATH")
    if raw:
        return Path(raw).expanduser()
    return None


def is_text_source(path: Path) -> bool:
    return path.suffix.lower() in TEXT_SUFFIXES


def add_source_file(sources: dict[str, SourceFile], path: Path, source_type: str) -> None:
    resolved = path.expanduser().resolve()
    stat = resolved.stat()
    sources[str(resolved)] = SourceFile(
        path=resolved,
        display=display_path(resolved),
        size_bytes=stat.st_size,
        modified_at=datetime.fromtimestamp(stat.st_mtime, timezone.utc),
        source_type=source_type,
    )


def classify_sources(sources: dict[str, SourceFile], manifest: dict[str, object]) -> list[SourceDelta]:
    manifest_sources = manifest.get("sources", {})
    if not isinstance(manifest_sources, dict):
        manifest_sources = {}

    entries_by_resolved: dict[str, tuple[str, dict[str, object]]] = {}
    for key, raw_entry in manifest_sources.items():
        if not isinstance(raw_entry, dict):
            raw_entry = {}
        if is_path_source(key):
            resolved = str(Path(key).expanduser().resolve(strict=False))
            entries_by_resolved[resolved] = (key, raw_entry)

    deltas: list[SourceDelta] = []
    seen_manifest: set[str] = set()
    for resolved, source in sorted(sources.items(), key=lambda item: item[1].display):
        matched_entry = entries_by_resolved.get(resolved)
        if matched_entry is not None:
            manifest_key, entry = matched_entry
            seen_manifest.add(manifest_key)
            status, reason = classify_existing_source(source, entry)
        else:
            manifest_key, entry = source.display, {}
            status, reason = "new", "not in manifest"
        deltas.append(SourceDelta(source=source, manifest_key=manifest_key, status=status, reason=reason, entry=entry))

    for key, raw_entry in sorted(manifest_sources.items()):
        if key in seen_manifest or not is_path_source(key):
            continue
        entry = raw_entry if isinstance(raw_entry, dict) else {}
        path = Path(key).expanduser()
        if not path.exists():
            deltas.append(
                SourceDelta(source=None, manifest_key=key, status="deleted", reason="missing on disk", entry=entry)
            )
    return deltas


def classify_existing_source(source: SourceFile, entry: dict[str, object]) -> tuple[str, str]:
    recorded_hash = str(entry.get("content_hash") or "")
    if recorded_hash:
        current_hash = hash_file(source.path)
        if current_hash != recorded_hash:
            return "modified", "content hash changed"
        recorded_modified = parse_datetime(str(entry.get("modified_at") or ""))
        if recorded_modified and source.modified_at > recorded_modified:
            return "touched", "mtime changed, content hash unchanged"
        return "unchanged", "content hash unchanged"

    baseline = parse_datetime(str(entry.get("modified_at") or entry.get("ingested_at") or ""))
    if baseline and source.modified_at > baseline:
        return "modified", "mtime newer than manifest"
    return "unchanged", "mtime unchanged"


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def scan_wiki_pages(vault: Path) -> tuple[dict[str, int], dict[str, int]]:
    page_counts = {category: 0 for category in WIKI_PAGE_DIRS}
    visibility = {"public": 0, "internal": 0, "pii": 0, "explicit_public": 0}
    for category in WIKI_PAGE_DIRS:
        root = vault / category
        if not root.exists():
            continue
        for path in root.rglob("*.md"):
            if not path.is_file():
                continue
            page_counts[category] += 1
            tags = read_frontmatter_tags(path)
            visibility_tags = {tag for tag in tags if tag.startswith("visibility/")}
            if "visibility/pii" in visibility_tags:
                visibility["pii"] += 1
            elif "visibility/internal" in visibility_tags:
                visibility["internal"] += 1
            else:
                visibility["public"] += 1
                if "visibility/public" in visibility_tags:
                    visibility["explicit_public"] += 1
    return page_counts, visibility


def read_frontmatter_tags(path: Path) -> set[str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError:
        return set()
    if not lines or lines[0].strip() != "---":
        return set()
    tags: set[str] = set()
    in_tags = False
    for line in lines[1:]:
        stripped = line.strip()
        if stripped == "---":
            break
        if stripped.startswith("tags:"):
            in_tags = True
            value = stripped[len("tags:") :].strip()
            tags.update(parse_tag_value(value))
            continue
        if in_tags and (line.startswith(" ") or line.startswith("-")):
            tags.update(parse_tag_value(stripped.lstrip("-").strip()))
            continue
        in_tags = False
    return tags


def parse_tag_value(value: str) -> set[str]:
    value = value.strip()
    if not value:
        return set()
    if value.startswith("[") and value.endswith("]"):
        value = value[1:-1]
    return {part.strip().strip('"').strip("'") for part in value.split(",") if part.strip()}


def latest_ingest_time(manifest: dict[str, object]) -> str | None:
    times: list[str] = []
    for value in (manifest.get("sources") or {}).values():
        if isinstance(value, dict) and value.get("ingested_at"):
            times.append(str(value["ingested_at"]))
    if times:
        return sorted(times)[-1]
    return str(manifest.get("last_updated") or "") or None


def recommend_status_action(
    *, manifest_exists: bool, ingested_count: int, ready_count: int, deleted_count: int
) -> str:
    if not manifest_exists or ingested_count == 0:
        return "Full ingest"
    if ready_count == 0 and deleted_count == 0:
        return "No action"
    if deleted_count >= 5 or deleted_count / max(ingested_count, 1) > 0.2:
        return "Lint first"
    if ready_count / max(ingested_count, 1) > 0.5:
        return "Rebuild"
    return "Append"


def render_delta_table(
    deltas: list[SourceDelta], headers: list[str], row_builder: Callable[[SourceDelta], list[str]], limit: int = 20
) -> str:
    if not deltas:
        return "_None._"
    rows = [row_builder(delta) for delta in deltas[:limit]]
    table = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    for row in rows:
        table.append("| " + " | ".join(escape_table_cell(str(cell)) for cell in row) + " |")
    if len(deltas) > limit:
        table.append(f"\n_Showing {limit} of {len(deltas)}._")
    return "\n".join(table)


def escape_table_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def format_bytes(size: int) -> str:
    units = ["B", "KB", "MB", "GB"]
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def format_datetime(value: datetime) -> str:
    return value.replace(microsecond=0).isoformat()


def parse_datetime(value: str) -> datetime | None:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def display_path(path: Path) -> str:
    home = Path.home().resolve()
    try:
        return "~/" + str(path.resolve().relative_to(home))
    except ValueError:
        return str(path)


def format_configured_paths(raw: str | None) -> str:
    if not raw:
        return "(none)"
    return ", ".join(display_path(path.expanduser()) for path in split_config_paths(raw)) or "(none)"


def source_type_from_entry(entry: dict[str, object]) -> str:
    return str(entry.get("source_type") or "file")


def is_path_source(value: str) -> bool:
    return not is_url(value) and "://" not in value


def infer_ingest_skill(workflow_args: list[str], data: bool = False) -> str:
    non_options = [arg for arg in workflow_args if not arg.startswith("-")]
    if data:
        return "data-ingest"
    if len(non_options) == 1 and is_url(non_options[0]):
        return "ingest-url"
    return "wiki-ingest"


def is_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def resolve_skills_source(explicit: Path | None) -> Path:
    candidates = []
    if explicit is not None:
        candidates.append(explicit.expanduser())
    if "A_INF_SKILLS_DIR" in os.environ:
        candidates.append(Path(os.environ["A_INF_SKILLS_DIR"]).expanduser())
    package_root = Path(__file__).resolve().parents[1]
    candidates.append(package_root / ".skills")
    candidates.append(Path.cwd() / ".skills")

    for candidate in candidates:
        resolved = candidate.resolve()
        if (resolved / "wiki-ingest" / "SKILL.md").exists():
            return resolved
    raise SystemExit("Could not find bundled skills. Pass --skills-source or set A_INF_SKILLS_DIR.")


def install_skills(source: Path, dest: Path, copy: bool = False) -> int:
    count = 0
    for skill_dir in sorted(path for path in source.iterdir() if path.is_dir()):
        if not (skill_dir / "SKILL.md").is_file():
            continue
        target = dest / skill_dir.name
        if target.exists() or target.is_symlink():
            if target.resolve() == skill_dir.resolve():
                continue
            continue
        if copy:
            shutil.copytree(skill_dir, target)
        else:
            target.symlink_to(skill_dir, target_is_directory=True)
        count += 1
    return count


def find_vault_root(start: Path) -> Path:
    for candidate in [start.resolve(), *start.resolve().parents]:
        if (candidate / ".a-inf" / "config.toml").exists() or (candidate / ".manifest.json").exists():
            return candidate
    return start.resolve()


def write_file_if_missing(path: Path, content: str) -> None:
    if not path.exists():
        path.write_text(content, encoding="utf-8")


def write_json_if_missing(path: Path, data: object) -> None:
    if not path.exists():
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def write_local_config(vault: Path, skills_source: Path) -> None:
    config_dir = vault / ".a-inf"
    config_dir.mkdir(exist_ok=True)
    config_path = config_dir / "config.toml"
    if config_path.exists():
        return
    config_path.write_text(
        "\n".join(
            [
                f'vault_path = "{vault}"',
                f'skills_source = "{skills_source}"',
                'link_format = "wikilink"',
                "",
            ]
        ),
        encoding="utf-8",
    )


def write_global_config(vault: Path) -> None:
    config_dir = Path.home() / ".obsidian-wiki"
    config_dir.mkdir(exist_ok=True)
    config_path = config_dir / "config"
    repo = Path(__file__).resolve().parents[1]
    config_path.write_text(
        f"OBSIDIAN_VAULT_PATH={vault}\nOBSIDIAN_WIKI_REPO={repo}\n",
        encoding="utf-8",
    )


def ensure_agents_section(path: Path) -> None:
    section = agents_section()
    if path.exists():
        current = path.read_text(encoding="utf-8")
        if "<!-- BEGIN A-INF -->" in current:
            return
        path.write_text(current.rstrip() + "\n\n" + section, encoding="utf-8")
    else:
        path.write_text("# Repository Instructions\n\n" + section, encoding="utf-8")


def ensure_gitignore_section(path: Path) -> None:
    required_entries = [".DS_Store", "_raw/", ".env", ".a-inf/"]
    section = "\n".join(["# a-inf local configuration", *required_entries, ""])
    if path.exists():
        current = path.read_text(encoding="utf-8")
        if "# a-inf local configuration" in current:
            current_entries = {line.strip() for line in current.splitlines()}
            missing = [entry for entry in required_entries if entry not in current_entries]
            if missing:
                path.write_text(current.rstrip() + "\n" + "\n".join(missing) + "\n", encoding="utf-8")
            return
        path.write_text(current.rstrip() + "\n\n" + section, encoding="utf-8")
    else:
        path.write_text(section, encoding="utf-8")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def index_template() -> str:
    return f"""---
title: Wiki Index
---

# Wiki Index

*This index is automatically maintained. Last updated: {now_iso()}*

## Concepts

*No pages yet. Use `a-inf ingest <source>` to add your first source.*

## Entities

## Skills

## References

## Synthesis

## Journal
"""


def log_template(vault: Path) -> str:
    return f"""---
title: Wiki Log
---

# Wiki Log

- [{now_iso()}] INIT vault_path="{vault}" categories=concepts,entities,skills,references,synthesis,journal
"""


def hot_template(vault: Path) -> str:
    return f"""---
title: Hot Cache
updated: {now_iso()}
---

# Hot Cache

*A short semantic snapshot of recent activity. Updated after every major write operation.*

## Recent Activity

- [{now_iso()}] INIT - vault created at {vault}

## Active Threads

*None yet - start ingesting sources to populate.*

## Key Takeaways

*None yet.*

## Flagged Contradictions

*None yet.*
"""


def taxonomy_template() -> str:
    return f"""---
title: Tag Taxonomy
category: references
tags: [taxonomy]
sources: []
created: {now_iso()}
updated: {now_iso()}
---

# Tag Taxonomy

Canonical tags will be added here as the vault grows.
"""


def env_template(vault: Path) -> str:
    return f"""OBSIDIAN_VAULT_PATH={vault}
OBSIDIAN_SOURCES_DIR=
OBSIDIAN_CATEGORIES=concepts,entities,skills,references,synthesis,journal
OBSIDIAN_MAX_PAGES_PER_INGEST=15
CODEX_HISTORY_PATH=
LINT_SCHEDULE=weekly
OBSIDIAN_LINK_FORMAT=wikilink
OBSIDIAN_RAW_DIR=_raw
QMD_WIKI_COLLECTION={vault.name}
QMD_PAPERS_COLLECTION={vault.name}
"""


def manifest_template() -> dict[str, object]:
    return {
        "version": 1,
        "last_updated": now_iso(),
        "sources": {},
        "projects": {},
        "stats": {
            "total_sources_ingested": 0,
            "total_pages": 0,
            "total_projects": 0,
            "last_full_rebuild": None,
        },
    }


def agents_section() -> str:
    return """<!-- BEGIN A-INF -->
## a-inf Vault

This repository is initialized as an a-inf Obsidian wiki vault.

- Prefer the `a-inf` CLI for workflows: `a-inf ingest`, `a-inf query`, `a-inf status`, `a-inf update`.
- Local skill instructions are symlinked under `.skills/<name>/SKILL.md`.
- The CLI may dispatch complex workflows to Codex; when it does, follow the selected skill file exactly.
- Keep `.manifest.json`, `index.md`, `log.md`, and `hot.md` current after write operations.
- Use `[[wikilinks]]` unless local config sets `OBSIDIAN_LINK_FORMAT=markdown`.
<!-- END A-INF -->
"""


if __name__ == "__main__":
    raise SystemExit(main())
