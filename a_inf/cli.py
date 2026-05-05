from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse


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
    "status": "wiki-status",
    "update": "wiki-update",
    "history": "codex-history-ingest",
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

    for name in [
        "ingest",
        "query",
        "status",
        "update",
        "history",
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

    print(f"Initialized a-inf vault: {vault}")
    print(f"Skills source: {skills_source}")
    print(f"Skills installed locally: {linked}")
    print("Next: a-inf ingest <source> or a-inf status")
    return 0


def cmd_dispatch(args: argparse.Namespace) -> int:
    alias = args.alias
    if alias == "ingest":
        skill = infer_ingest_skill(args.args, data=getattr(args, "data", False))
    else:
        skill = SKILL_ALIASES[alias]
    dispatch = build_dispatch(skill, args.args)
    return run_dispatch(dispatch, args)


def cmd_skill(args: argparse.Namespace) -> int:
    dispatch = build_dispatch(args.skill, args.args)
    return run_dispatch(dispatch, args)


def run_dispatch(dispatch: Dispatch, args: argparse.Namespace) -> int:
    if args.print_prompt or args.no_codex:
        print(dispatch.prompt)
        return 0

    codex_bin = shutil.which(args.codex_bin)
    if codex_bin is None:
        print("Codex executable not found. Re-run with --print-prompt or install Codex CLI.", file=sys.stderr)
        print(dispatch.prompt)
        return 127

    return subprocess.call([codex_bin, "exec", dispatch.prompt], cwd=find_vault_root(Path.cwd()))


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
        "whenever the selected workflow writes to the vault."
    )
    return Dispatch(skill=skill, prompt=prompt)


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
    section = "\n".join(
        [
            "# a-inf local configuration",
            ".env",
            ".a-inf/config.toml",
            "",
        ]
    )
    if path.exists():
        current = path.read_text(encoding="utf-8")
        if "# a-inf local configuration" in current:
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
QMD_WIKI_COLLECTION=
QMD_PAPERS_COLLECTION=
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
