from __future__ import annotations

import argparse
from collections.abc import Callable
import hashlib
import json
import os
import random
import shutil
import subprocess
import sys
import textwrap
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import tomllib
from typing import Any
from urllib.parse import urlparse

from a_inf.managed_files import ensure_managed_tag, ensure_vault_managed_tags, managed_tags
from a_inf.qmd import ensure_qmd_collection, ensure_qmd_state_dirs, qmd_env, qmd_state_dirs, sync_qmd


LOCAL_SKILLS_DIR = Path(".agents") / "skills"
VSCODE_TEMPLATE_DIR = Path(__file__).resolve().parent / "templates" / "vscode"
VSCODE_TERMINAL_BACKGROUND_COLORS = [
    "#4B004B",
    "#5A005A",
    "#660066",
    "#720072",
    "#7B007B",
    "#800080",
    "#8B008B",
]

VAULT_DIRS = [
    "concepts",
    "entities",
    "references",
    "synthesis",
    "projects",
    "ideas",
    "_archives",
    "_raw",
    "_sources",
    "_runs",
    "_meta",
    ".obsidian",
    ".vscode",
    str(LOCAL_SKILLS_DIR),
]

TRACKED_SCAFFOLD_DIRS = [
    "concepts",
    "entities",
    "references",
    "synthesis",
    "projects",
    "ideas",
    "_archives",
]

SKILL_ALIASES = {
    "ingest": "wiki-ingest",
    "query": "wiki-query",
    "update": "wiki-update",
    "history": "codex-history-ingest",
    "insights": "wiki-insights",
    "lint": "wiki-lint",
    "doctor": "wiki-doctor",
    "rebuild": "wiki-rebuild",
    "export": "wiki-export",
    "research": "wiki-research",
    "capture": "wiki-capture",
    "synthesize": "wiki-synthesize",
    "dashboard": "wiki-dashboard",
    "colorize": "wiki-colorize",
    "fixlink": "wiki-fixlink",
    "tags": "wiki-tags",
    "ideate": "wiki-ideate",
}

COMMAND_HELP = {
    "init": "Initialize a vault; writes scaffold files, config, Obsidian/VS Code settings, skill links, and an initial commit.",
    "info": "Show configuration; prints vault paths, source roots, skill roots, and QMD settings.",
    "status": "Show ingest state; prints page counts, source deltas, manifest state, and next-action guidance.",
    "ingest": "Import sources; writes wiki pages, source archives, manifest/index/log/hot updates, and QMD state.",
    "query": "Answer from the compiled wiki; prints a cited answer and saves it under _runs/query-* by default.",
    "insights": "Analyze wiki graph structure; writes _runs/insights-* output and prints hubs, bridges, and orphans.",
    "lint": "Audit wiki health; prints or saves link, metadata, stale-page, orphan, and semantic findings.",
    "doctor": "Run bundled wiki health checks and safe cleanup phases, then save a consolidated report.",
    "fixlink": "Add or remove wikilinks; edits pages when changes apply and prints a validation report.",
    "synthesize": "Find synthesis gaps; writes run packets/reports and any accepted synthesis pages.",
    "dashboard": "Create Obsidian Bases dashboards; writes _meta/*.base and prints the dashboard report.",
    "colorize": "Configure graph colors; edits .obsidian/graph.json, writes a backup, and prints color groups.",
    "tags": "Audit or normalize tags; writes a tag plan or applies one with --fix, then prints a tag report.",
    "ideate": "Create an agent handoff idea packet; writes one Markdown file under ideas/ and prints its path.",
    "update": "Sync project knowledge into the wiki; Codex updates project pages and tracking files.",
    "history": "Ingest Codex history; Codex mines local conversations and writes durable wiki knowledge.",
    "rebuild": "Archive, rebuild, or restore wiki state; Codex performs the selected workflow after confirmation.",
    "export": "Export the wiki graph; Codex writes JSON, GraphML, Cypher, and HTML export files.",
    "research": "Research a topic; Codex writes source, concept, entity, and synthesis pages plus tracking updates.",
    "capture": "Capture the current discussion; Codex writes a structured note and updates wiki tracking files.",
    "skill": "Dispatch any bundled skill by name; output depends on the selected skill.",
}

COMMAND_GROUPS = [
    ("CLI-native commands", ["init", "info", "status"]),
    (
        "Python-owned workflows",
        [
            "ingest",
            "query",
            "insights",
            "lint",
            "doctor",
            "fixlink",
            "synthesize",
            "dashboard",
            "colorize",
            "tags",
            "ideate",
        ],
    ),
    ("Thin Codex skill dispatchers", ["update", "history", "rebuild", "export", "research", "capture", "skill"]),
]

QMD_SYNC_SKILLS = {
    "wiki-ingest",
    "wiki-update",
    "codex-history-ingest",
    "wiki-history-ingest",
    "wiki-rebuild",
    "wiki-research",
    "wiki-capture",
    "wiki-synthesize",
    "wiki-dashboard",
    "wiki-colorize",
    "wiki-fixlink",
    "wiki-tags",
}

INIT_COMMIT_PATHS = [
    *[f"{dirname}/.gitkeep" for dirname in TRACKED_SCAFFOLD_DIRS],
    "index.md",
    "log.md",
    "hot.md",
    "_insights.md",
    "_meta/taxonomy.md",
    ".manifest.json",
    ".obsidian/app.json",
    ".obsidian/appearance.json",
    ".obsidian/community-plugins.json",
    ".obsidian/graph.json",
    ".vscode/settings.json",
    ".vscode/tasks.json",
    str(LOCAL_SKILLS_DIR),
    "AGENTS.md",
    ".gitignore",
]


@dataclass(frozen=True)
class Dispatch:
    skill: str
    prompt: str


class AInfArgumentParser(argparse.ArgumentParser):
    def __init__(
        self,
        *args: object,
        command_groups: list[tuple[str, list[str]]] | None = None,
        command_help: dict[str, str] | None = None,
        **kwargs: object,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.command_groups = command_groups or []
        self.command_help = command_help or {}

    def format_help(self) -> str:
        if not self.command_groups:
            return super().format_help()

        usage_formatter = self._get_formatter()
        usage_formatter.add_usage(self.usage, self._actions, self._mutually_exclusive_groups)

        option_formatter = self._get_formatter()
        option_formatter.start_section("options")
        option_formatter.add_arguments(self._optionals._group_actions)
        option_formatter.end_section()

        return (
            usage_formatter.format_help().rstrip()
            + "\n\n"
            + option_formatter.format_help().rstrip()
            + "\n\n"
            + self._format_command_groups()
        )

    def _format_command_groups(self) -> str:
        command_width = max(len(command) for _, commands in self.command_groups for command in commands)
        width = max(60, shutil.get_terminal_size(fallback=(100, 24)).columns)
        hanging_indent = " " * (4 + command_width + 2)
        lines = ["commands:"]
        for title, commands in self.command_groups:
            lines.append(f"  {title}:")
            for command in commands:
                prefix = f"    {command:<{command_width}}  "
                wrapped = textwrap.wrap(
                    self.command_help.get(command, ""),
                    width=max(30, width - len(prefix)),
                    initial_indent=prefix,
                    subsequent_indent=hanging_indent,
                )
                lines.extend(wrapped or [prefix.rstrip()])
        return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


def build_parser() -> argparse.ArgumentParser:
    parser = AInfArgumentParser(prog="a-inf", command_groups=COMMAND_GROUPS, command_help=COMMAND_HELP)
    parser.add_argument("--version", action="version", version="a-inf 0.1.0")

    sub = parser.add_subparsers(dest="command", required=True)

    init_parser = sub.add_parser("init", help=COMMAND_HELP["init"])
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

    status_parser = sub.add_parser("status", help=COMMAND_HELP["status"])
    status_parser.add_argument(
        "--insights",
        action="store_true",
        help="Run graph insights instead of the deterministic status report.",
    )
    status_parser.add_argument(
        "--json",
        action="store_true",
        help="Print the deterministic status report as JSON.",
    )
    status_parser.add_argument(
        "args",
        nargs="*",
        help="Optional compatibility arguments. Insight-related words route to wiki-insights.",
    )
    add_dispatch_options(status_parser)
    status_parser.set_defaults(func=cmd_status)

    info_parser = sub.add_parser("info", help=COMMAND_HELP["info"])
    info_parser.set_defaults(func=cmd_info)

    for name in [
        "ingest",
        "query",
        "update",
        "history",
        "insights",
        "lint",
        "doctor",
        "rebuild",
        "export",
        "research",
        "capture",
        "synthesize",
        "dashboard",
        "colorize",
        "fixlink",
        "tags",
        "ideate",
    ]:
        cmd = sub.add_parser(name, help=COMMAND_HELP[name])
        if name == "ingest":
            cmd.add_argument(
                "--data",
                action="store_true",
                help="Compatibility flag; exports, logs, and transcripts use the normal hybrid ingest path.",
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
            strategy = cmd.add_mutually_exclusive_group()
            strategy.add_argument(
                "--once",
                action="store_true",
                help="Ingest only the first selected source. This is the default.",
            )
            strategy.add_argument(
                "--batch",
                action="store_true",
                help="Ingest all selected sources together in one semantic planning batch.",
            )
        if name == "lint":
            cmd.add_argument(
                "--json",
                action="store_true",
                help="Print the final lint health packet as JSON instead of Markdown.",
            )
            cmd.add_argument(
                "--semantic-scope",
                choices=["one-hop", "broad"],
                default="one-hop",
                help="How much vault context Codex may read during semantic review. Default: one-hop.",
            )
            cmd.add_argument(
                "--no-log",
                action="store_true",
                help="Do not append the default LINT entry to log.md.",
            )
            cmd.add_argument(
                "--save",
                action="store_true",
                default=True,
                help="Save the rendered lint findings as Markdown inside the lint run folder under _runs/. This is the default.",
            )
            cmd.add_argument(
                "--no-save",
                dest="save",
                action="store_false",
                help="Print the lint findings without saving them to the vault.",
            )
            cmd.add_argument(
                "--output",
                default=None,
                help="Vault-relative Markdown path for saved output. Defaults to _runs/lint-<timestamp>/lint-findings.md.",
            )
        if name == "doctor":
            cmd.add_argument(
                "--json",
                action="store_true",
                help="Print the consolidated doctor packet as JSON instead of Markdown.",
            )
            cmd.add_argument(
                "--dry-run",
                action="store_true",
                help="Run doctor phases without applying page edits.",
            )
            cmd.add_argument(
                "--fix",
                action="store_true",
                help="Include semantic fixlink repair in addition to deterministic broken-link cleanup.",
            )
            cmd.add_argument(
                "--full",
                action="store_true",
                help="Run the fuller doctor flow: semantic fixlink plus Codex tag planning unless --no-codex is set.",
            )
            cmd.add_argument(
                "--apply-tags",
                action="store_true",
                help="Apply the generated or latest validated tag plan. Intended for reviewed plans.",
            )
            cmd.add_argument(
                "--semantic-scope",
                choices=["one-hop", "broad"],
                default="one-hop",
                help="Semantic review scope forwarded to child workflows. Default: one-hop.",
            )
            cmd.add_argument(
                "--no-log",
                action="store_true",
                help="Do not append DOCTOR to log.md or forward logging to child write phases.",
            )
        if name == "fixlink":
            cmd.add_argument(
                "--json",
                action="store_true",
                help="Print the final fixlink report as JSON instead of Markdown.",
            )
            cmd.add_argument(
                "--dry-run",
                action="store_true",
                help="Validate the planned fixlink changes without applying edits.",
            )
            cmd.add_argument(
                "--remove-broken",
                action="store_true",
                help="Deterministically remove wikilinks whose targets do not resolve to existing pages.",
            )
            cmd.add_argument(
                "--no-log",
                action="store_true",
                help="Do not append FIXLINK to log.md or update hot.md.",
            )
        if name == "tags":
            cmd.add_argument(
                "--json",
                action="store_true",
                help="Print the final tag audit or normalization report as JSON instead of Markdown.",
            )
            cmd.add_argument(
                "--fix",
                action="store_true",
                help="Apply the latest edited tag_plan.json from a previous a-inf tags run.",
            )
            cmd.add_argument(
                "--plan",
                type=Path,
                default=None,
                help="Apply a specific tag_plan.json when used with --fix.",
            )
            cmd.add_argument(
                "--no-log",
                action="store_true",
                help="Do not append TAG_NORMALIZE to log.md or update hot.md when using --fix.",
            )
        if name == "colorize":
            cmd.add_argument(
                "--mode",
                choices=["by-tag", "by-category", "by-visibility", "combined", "custom", "clear", "undo"],
                default=None,
                help="Deterministic graph color mode. Default: by-tag.",
            )
            cmd.add_argument(
                "--groups-json",
                default=None,
                help="JSON custom color mapping for --mode custom.",
            )
            cmd.add_argument(
                "--json",
                action="store_true",
                help="Print the final colorize report as JSON instead of Markdown.",
            )
            cmd.add_argument(
                "--no-log",
                action="store_true",
                help="Do not append GRAPH_COLORIZE to log.md.",
            )
        if name == "synthesize":
            cmd.add_argument(
                "--vscode",
                action="store_true",
                help="Open generated synthesize output in VS Code after the workflow completes.",
            )
            cmd.add_argument(
                "--vscode-bin",
                default="code",
                help="VS Code executable to use for --vscode. Default: code.",
            )
            cmd.add_argument(
                "--json",
                action="store_true",
                help="Print the final synthesize report as JSON instead of Markdown.",
            )
            cmd.add_argument(
                "--dry-run",
                action="store_true",
                help="Run Codex and validate the synthesis plan without applying edits.",
            )
            cmd.add_argument(
                "--no-log",
                action="store_true",
                help="Do not append WIKI_SYNTHESIZE to log.md.",
            )
        if name == "dashboard":
            cmd.add_argument(
                "--recipe",
                choices=[
                    "content-index",
                    "entities",
                    "recent-ingests",
                    "stale-pages",
                    "projects",
                    "tag-cloud",
                    "research",
                ],
                default=None,
                help="Deterministic dashboard recipe. Defaults to content-index when no request text is given.",
            )
            cmd.add_argument("--folder", default=None, help="Add a folder filter to the dashboard.")
            cmd.add_argument("--tag", default=None, help="Add a tag filter to the dashboard.")
            cmd.add_argument(
                "--view",
                choices=["table", "cards", "list"],
                default=None,
                help="Obsidian Bases view type. Default: table.",
            )
            cmd.add_argument("--name", default=None, help="Output slug for _meta/<name>.base.")
            cmd.add_argument("--title", default=None, help="Display title for the default view.")
            cmd.add_argument("--limit", type=int, default=None, help="Optional view result limit.")
            cmd.add_argument(
                "--json",
                action="store_true",
                help="Print the final dashboard report as JSON instead of Markdown.",
            )
            cmd.add_argument(
                "--dry-run",
                action="store_true",
                help="Validate and render the dashboard without writing the .base file or log.",
            )
            cmd.add_argument(
                "--no-log",
                action="store_true",
                help="Do not append WIKI_DASHBOARD to log.md.",
            )
        if name == "insights":
            cmd.add_argument(
                "--vscode",
                action="store_true",
                help="Open generated insights output in VS Code after the workflow completes.",
            )
            cmd.add_argument(
                "--vscode-bin",
                default="code",
                help="VS Code executable to use for --vscode. Default: code.",
            )
            cmd.add_argument(
                "--json",
                action="store_true",
                help="Print the final insights report as JSON instead of Markdown.",
            )
            cmd.add_argument(
                "--no-log",
                action="store_true",
                help="Do not append WIKI_INSIGHTS to log.md.",
            )
        if name == "ideate":
            cmd.add_argument(
                "--mode",
                choices=["inline", "vscode"],
                default="inline",
                help="How to provide the idea. Default: inline.",
            )
            cmd.add_argument(
                "--vscode",
                dest="mode",
                action="store_const",
                const="vscode",
                help="Open a temporary Markdown idea draft in VS Code, then run it after the file is closed.",
            )
            cmd.add_argument(
                "--vscode-bin",
                default="code",
                help="VS Code executable to use for --mode vscode. Default: code.",
            )
            cmd.add_argument(
                "--entry",
                action="append",
                default=[],
                help="Relevant wiki page path, title, stem, or wikilink-ish name. Repeat for multiple entries.",
            )
            cmd.add_argument(
                "--json",
                action="store_true",
                help="Print the final ideation report as JSON instead of Markdown.",
            )
        if name == "query":
            cmd.add_argument(
                "--mode",
                choices=["inline", "vscode"],
                default="inline",
                help="How to provide the query. Default: inline.",
            )
            cmd.add_argument(
                "--vscode",
                dest="mode",
                action="store_const",
                const="vscode",
                help="Open a temporary Markdown query draft in VS Code, then run it after the file is closed.",
            )
            cmd.add_argument(
                "--vscode-bin",
                default="code",
                help="VS Code executable to use for --mode vscode. Default: code.",
            )
            cmd.add_argument(
                "--save",
                action="store_true",
                default=True,
                help="Save the synthesized answer as Markdown inside a query run folder under _runs/. This is the default.",
            )
            cmd.add_argument(
                "--no-save",
                dest="save",
                action="store_false",
                help="Print the synthesized answer without saving it to the vault.",
            )
            cmd.add_argument(
                "--output",
                default=None,
                help="Vault-relative Markdown path for saved output. Defaults to _runs/query-<timestamp>-<question>/answer.md.",
            )
        cmd.add_argument("args", nargs="*", help="Arguments passed to the workflow.")
        add_dispatch_options(cmd)
        cmd.set_defaults(func=cmd_dispatch, alias=name)

    skill_parser = sub.add_parser("skill", help=COMMAND_HELP["skill"])
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
    git_result = initialize_git_repo(vault)
    if git_result != 0:
        return git_result

    for dirname in VAULT_DIRS:
        (vault / dirname).mkdir(parents=True, exist_ok=True)

    ensure_gitkeep_files(vault)
    write_file_if_missing(vault / "index.md", index_template())
    write_file_if_missing(vault / "log.md", log_template(vault))
    write_file_if_missing(vault / "hot.md", hot_template(vault))
    write_file_if_missing(vault / "_meta" / "taxonomy.md", taxonomy_template())
    ensure_vault_managed_tags(vault, include_agents=False)
    write_json_if_missing(vault / ".manifest.json", manifest_template())
    write_json_if_missing(
        vault / ".obsidian" / "app.json",
        {
            "strictLineBreaks": False,
            "showFrontmatter": False,
            "defaultViewMode": "preview",
            "livePreview": True,
            "promptDelete": False,
            "showUnsupportedFiles": True,
        },
    )
    write_json_if_missing(vault / ".obsidian" / "appearance.json", {"baseFontSize": 16})
    write_json_if_missing(vault / ".obsidian" / "community-plugins.json", ["obsidian-git", "lean-terminal"])
    write_json_if_missing(vault / ".obsidian" / "graph.json", graph_template())
    install_vscode_templates(vault)
    write_local_config(vault, skills_source)
    write_file_if_missing(vault / ".env", env_template(vault))

    linked = install_skills(skills_source, vault / LOCAL_SKILLS_DIR, copy=args.copy_skills)

    if not args.no_agents:
        ensure_agents_section(vault / "AGENTS.md")
        ensure_managed_tag(vault / "AGENTS.md", "Repository Instructions")
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

    commit_result = commit_vault_scaffold(vault)
    if commit_result != 0:
        return commit_result

    print(f"Initialized a-inf vault: {vault}")
    print(f"Skills source: {skills_source}")
    print(f"Skills installed locally: {linked}")
    print("Next: a-inf ingest <source> or a-inf status")
    return 0


def initialize_git_repo(vault: Path) -> int:
    try:
        result = subprocess.run(
            ["git", "init"],
            cwd=vault,
            text=True,
            capture_output=True,
            check=False,
        )
    except FileNotFoundError:
        print("git executable not found. Install git and re-run a-inf init.", file=sys.stderr)
        return 127

    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        message = f"git init failed in {vault}"
        if detail:
            message = f"{message}: {detail}"
        print(message, file=sys.stderr)
    return result.returncode


def ensure_gitkeep_files(vault: Path) -> None:
    for dirname in TRACKED_SCAFFOLD_DIRS:
        write_file_if_missing(vault / dirname / ".gitkeep", "")


def install_vscode_templates(vault: Path) -> None:
    settings_path = vault / ".vscode" / "settings.json"
    if write_bytes_if_missing(settings_path, (VSCODE_TEMPLATE_DIR / "settings.json").read_bytes()):
        add_random_vscode_terminal_background(settings_path)
    write_bytes_if_missing(vault / ".vscode" / "tasks.json", (VSCODE_TEMPLATE_DIR / "tasks.json").read_bytes())


def add_random_vscode_terminal_background(settings_path: Path) -> None:
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    color_customizations = settings.setdefault("workbench.colorCustomizations", {})
    color_customizations["terminal.background"] = random.choice(VSCODE_TERMINAL_BACKGROUND_COLORS)
    settings_path.write_text(json.dumps(settings, indent=4) + "\n", encoding="utf-8")


def commit_vault_scaffold(vault: Path) -> int:
    paths = [path for path in INIT_COMMIT_PATHS if (vault / path).exists()]
    if not paths:
        return 0

    add_result = run_git_command(vault, ["add", "--", *paths], "git add failed")
    if add_result != 0:
        return add_result

    diff_result = run_git_command(
        vault,
        ["diff", "--cached", "--quiet"],
        "git diff failed",
        ok_returncodes={0, 1},
    )
    if diff_result == 0:
        return 0
    if diff_result != 1:
        return diff_result

    return run_git_command(
        vault,
        ["commit", "-m", "Initialize a-inf vault"],
        "git commit failed",
        env=git_commit_env(vault),
    )


def run_git_command(
    vault: Path,
    args: list[str],
    failure_message: str,
    env: dict[str, str] | None = None,
    ok_returncodes: set[int] | None = None,
) -> int:
    ok = ok_returncodes or {0}
    command = ["git", *args]
    try:
        result = subprocess.run(
            command,
            cwd=vault,
            text=True,
            capture_output=True,
            check=False,
            env=env,
        )
    except FileNotFoundError:
        print("git executable not found. Install git and re-run a-inf init.", file=sys.stderr)
        return 127

    if result.returncode not in ok:
        detail = (result.stderr or result.stdout).strip()
        message = f"{failure_message} in {vault}"
        if detail:
            message = f"{message}: {detail}"
        print(message, file=sys.stderr)
    return result.returncode


def git_commit_env(vault: Path) -> dict[str, str] | None:
    configured_name = git_config_value(vault, "user.name")
    configured_email = git_config_value(vault, "user.email")
    if configured_name and configured_email:
        return None

    env = os.environ.copy()
    name = env.get("GIT_AUTHOR_NAME") or env.get("GIT_COMMITTER_NAME") or configured_name or "a-inf"
    email = env.get("GIT_AUTHOR_EMAIL") or env.get("GIT_COMMITTER_EMAIL") or configured_email or "a-inf@example.invalid"
    env.setdefault("GIT_AUTHOR_NAME", name)
    env.setdefault("GIT_AUTHOR_EMAIL", email)
    env.setdefault("GIT_COMMITTER_NAME", name)
    env.setdefault("GIT_COMMITTER_EMAIL", email)
    return env


def git_config_value(vault: Path, key: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "config", "--get", key],
            cwd=vault,
            text=True,
            capture_output=True,
            check=False,
        )
    except FileNotFoundError:
        return None
    value = result.stdout.strip()
    return value or None


def cmd_dispatch(args: argparse.Namespace) -> int:
    alias = args.alias
    if alias == "ingest":
        skill = infer_ingest_skill(args.args, data=getattr(args, "data", False))
        if skill == "wiki-ingest":
            from a_inf.ingest import run_hybrid_ingest

            return run_hybrid_ingest(args, find_vault_root(Path.cwd()))
    elif alias == "query":
        from a_inf.query import run_query

        vault = find_vault_root(Path.cwd())
        return run_query(args, vault, load_wiki_config(vault))
    elif alias == "lint":
        from a_inf.lint import run_lint

        vault = find_vault_root(Path.cwd())
        return run_lint(args, vault, load_wiki_config(vault))
    elif alias == "doctor":
        from a_inf.doctor import run_doctor

        vault = find_vault_root(Path.cwd())
        return run_doctor(args, vault, load_wiki_config(vault))
    elif alias == "fixlink":
        from a_inf.fixlink import run_fixlink

        vault = find_vault_root(Path.cwd())
        return run_fixlink(args, vault, load_wiki_config(vault))
    elif alias == "synthesize":
        from a_inf.synthesize import run_synthesize

        vault = find_vault_root(Path.cwd())
        return run_synthesize(args, vault, load_wiki_config(vault))
    elif alias == "insights":
        from a_inf.insights import run_insights

        vault = find_vault_root(Path.cwd())
        return run_insights(args, vault, load_wiki_config(vault))
    elif alias == "tags":
        from a_inf.tags import run_tags

        vault = find_vault_root(Path.cwd())
        return run_tags(args, vault, load_wiki_config(vault))
    elif alias == "colorize":
        from a_inf.colorize import run_colorize

        vault = find_vault_root(Path.cwd())
        return run_colorize(args, vault, load_wiki_config(vault))
    elif alias == "dashboard":
        from a_inf.dashboard import run_dashboard

        vault = find_vault_root(Path.cwd())
        return run_dashboard(args, vault, load_wiki_config(vault))
    elif alias == "ideate":
        from a_inf.ideate import run_ideate

        vault = find_vault_root(Path.cwd())
        return run_ideate(args, vault, load_wiki_config(vault))
    else:
        skill = SKILL_ALIASES[alias]
    dispatch = build_dispatch(skill, args.args)
    return run_dispatch(dispatch, args)


def cmd_skill(args: argparse.Namespace) -> int:
    dispatch = build_dispatch(args.skill, args.args)
    return run_dispatch(dispatch, args)


def cmd_status(args: argparse.Namespace) -> int:
    vault = find_vault_root(Path.cwd())
    if not has_a_inf_structure(vault):
        print(
            f"Not an a-inf vault: {Path.cwd()}. Run `a-inf init` first or cd into a folder containing .a-inf.",
            file=sys.stderr,
        )
        return 1

    insight_terms = " ".join(getattr(args, "args", [])).lower()
    if getattr(args, "insights", False) or any(
        term in insight_terms
        for term in ["insight", "hubs", "hub", "central", "structure", "connected", "bridge"]
    ):
        from a_inf.insights import run_insights

        return run_insights(args, vault, load_wiki_config(vault))

    packet = build_status_packet(vault)
    if getattr(args, "json", False):
        print(json.dumps(packet, indent=2))
    else:
        print(build_status_report(vault, packet))
    return 0


def cmd_info(_args: argparse.Namespace) -> int:
    from a_inf.ingest import load_wiki_config as load_ingest_config
    from a_inf.ingest import print_info

    vault = find_vault_root(Path.cwd())
    print_info(vault, load_ingest_config(vault))
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
    skill_path = vault / LOCAL_SKILLS_DIR / skill / "SKILL.md"
    args_text = " ".join(workflow_args).strip()
    if not skill_path.exists():
        legacy_skill_path = vault / ".skills" / skill / "SKILL.md"
        if legacy_skill_path.exists():
            skill_path = legacy_skill_path
        else:
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


WIKI_PAGE_DIRS = ["concepts", "entities", "references", "synthesis", "projects"]
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
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
PDF_SUFFIXES = {".pdf"}
SUPPORTED_SOURCE_SUFFIXES = TEXT_SUFFIXES | IMAGE_SUFFIXES | PDF_SUFFIXES


def build_status_packet(vault: Path) -> dict[str, Any]:
    config = load_wiki_config(vault)
    manifest, manifest_exists = read_manifest(vault)
    sources = scan_sources(vault, config, manifest)
    deltas = classify_sources(sources, manifest)
    page_counts, visibility = scan_wiki_pages(vault)

    source_entries = manifest.get("sources", {})
    if not isinstance(source_entries, dict):
        source_entries = {}
    projects = manifest.get("projects", {})
    if not isinstance(projects, dict):
        projects = {}
    stats = manifest.get("stats", {})
    if not isinstance(stats, dict):
        stats = {}
    total_ingested = int(stats.get("total_sources_ingested") or len(source_entries))
    total_projects = int(stats.get("total_projects") or len(projects))
    last_ingest = latest_ingest_time(manifest)
    archived_sources = sum(
        1 for entry in source_entries.values() if isinstance(entry, dict) and entry.get("archive_dir")
    )

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
    raw_ingest = build_raw_ingest_status(vault, config, manifest)
    jobs = build_status_jobs(raw_ingest)
    agent = {
        "running": 1 if raw_ingest["in_progress"] else 0,
        "runnable": jobs["runnable"],
        "queued": jobs["queued"],
        "blocked": jobs["blocked"],
    }
    warnings: list[str] = []
    return {
        "schema_version": 1,
        "health": "ok" if not warnings and jobs["blocked"] == 0 else "warning",
        "warnings": warnings,
        "vault": str(vault),
        "overview": {
            "total_wiki_pages": total_pages,
            "category_count": category_count,
            "page_counts": page_counts,
            "visibility": visibility,
            "total_sources_ingested": total_ingested,
            "archived_source_detail_layers": archived_sources,
            "projects_tracked": total_projects,
            "last_ingest": last_ingest,
            "configured_document_sources": format_configured_paths(config.get("OBSIDIAN_SOURCES_DIR")),
            "codex_history_path": format_configured_paths(
                os.environ.get("CODEX_HISTORY_PATH") or config.get("CODEX_HISTORY_PATH")
            ),
            "manifest_exists": manifest_exists,
        },
        "agent": agent,
        "jobs": jobs,
        "raw_ingest": raw_ingest,
        "source_deltas": {
            "ready_count": ready_count,
            "recommendation": recommendation,
            "counts": {
                "new": len(new),
                "modified": len(modified),
                "touched": len(touched),
                "unchanged": len(unchanged),
                "deleted": len(deleted),
            },
            "items": {
                "new": [source_delta_to_json(delta) for delta in new],
                "modified": [source_delta_to_json(delta) for delta in modified],
                "touched": [source_delta_to_json(delta) for delta in touched],
                "unchanged": [source_delta_to_json(delta) for delta in unchanged],
                "deleted": [source_delta_to_json(delta) for delta in deleted],
            },
        },
    }


def build_status_report(vault: Path, packet: dict[str, Any] | None = None) -> str:
    if packet is None:
        packet = build_status_packet(vault)
    overview = packet["overview"]
    visibility = overview["visibility"]
    source_deltas = packet["source_deltas"]
    delta_counts = source_deltas["counts"]
    raw_ingest = packet["raw_ingest"]
    jobs = packet["jobs"]
    agent = packet["agent"]

    lines = [
        "# Wiki Status",
        "",
        "## Overview",
        f"- **Vault:** {vault}",
        f"- **Health:** {packet['health']}",
        f"- **Total wiki pages:** {overview['total_wiki_pages']} across {overview['category_count']} categories",
    ]
    if visibility["internal"] or visibility["pii"] or visibility["explicit_public"]:
        lines.append(
            f"- **Page visibility:** {visibility['public']} public, "
            f"{visibility['internal']} internal, {visibility['pii']} pii"
        )
    lines.extend(
        [
            f"- **Total sources ingested:** {overview['total_sources_ingested']}",
            f"- **Archived source detail layers:** {overview['archived_source_detail_layers']}",
            f"- **Projects tracked:** {overview['projects_tracked']}",
            f"- **Last ingest:** {overview['last_ingest'] or 'never'}",
            f"- **Configured document sources:** {overview['configured_document_sources']}",
            f"- **Codex history path:** {overview['codex_history_path']}",
            "",
            "## Agent Queue",
            f"- **Agent:** {agent['running']} running, {agent['runnable']} runnable, "
            f"{agent['queued']} queued, {agent['blocked']} blocked",
            f"- **Raw ingest:** {raw_ingest['pending_count']} pending in {raw_ingest['raw_dir']}",
            f"- **Raw ingest in progress:** {'yes' if raw_ingest['in_progress'] else 'no'}",
        ]
    )
    if raw_ingest.get("next_file"):
        next_file = raw_ingest["next_file"]
        lines.append(
            f"- **Next raw file:** {next_file['relpath']} "
            f"({format_bytes(int(next_file['size_bytes']))}, {next_file['mtime']})"
        )
    if raw_ingest.get("last_ingest"):
        last_raw = raw_ingest["last_ingest"]
        lines.append(
            f"- **Last raw ingest:** {last_raw['source_relpath']} "
            f"{last_raw['status']} at {last_raw['finished_at']}"
        )
    if jobs["items"]:
        job_text = ", ".join(
            f"{item['id']} ({'runnable' if item['runnable'] else item['reason']})" for item in jobs["items"]
        )
        lines.append(f"- **Jobs:** {job_text}")
    else:
        lines.append("- **Jobs:** none")

    lines.extend(
        [
            "",
            "## Delta (what's changed since last ingest)",
            "",
            f"### New sources (never ingested): {delta_counts['new']}",
            render_status_delta_items(source_deltas["items"]["new"], ["source", "source_type", "size_bytes"]),
            "",
            f"### Modified sources (need re-ingesting): {delta_counts['modified']}",
            render_status_delta_items(source_deltas["items"]["modified"], ["source", "ingested_at", "modified_at", "reason"]),
            "",
            f"### Touched sources (content unchanged): {delta_counts['touched']}",
            render_status_delta_items(source_deltas["items"]["touched"], ["source", "reason"]),
            "",
            f"### Deleted sources (ingested but gone): {delta_counts['deleted']}",
            render_status_delta_items(source_deltas["items"]["deleted"], ["manifest_key", "ingested_at"]),
            "",
            "## Summary",
            f"- **Ready to ingest:** {delta_counts['new']} new + {delta_counts['modified']} modified = "
            f"{source_deltas['ready_count']} sources",
            f"- **Up to date:** {delta_counts['unchanged']} unchanged",
            f"- **Touched but identical:** {delta_counts['touched']}",
            f"- **Deleted:** {delta_counts['deleted']}",
            f"- **Recommendation:** {source_deltas['recommendation']}",
        ]
    )
    return "\n".join(lines)


def source_delta_to_json(delta: SourceDelta) -> dict[str, Any]:
    data: dict[str, Any] = {
        "manifest_key": delta.manifest_key,
        "status": delta.status,
        "reason": delta.reason,
        "ingested_at": delta.entry.get("ingested_at") if isinstance(delta.entry, dict) else None,
    }
    if delta.source is not None:
        data.update(
            {
                "source": delta.source.display,
                "path": str(delta.source.path),
                "source_type": delta.source.source_type,
                "size_bytes": delta.source.size_bytes,
                "modified_at": status_datetime(delta.source.modified_at),
            }
        )
    else:
        data.update(
            {
                "source": None,
                "source_type": source_type_from_entry(delta.entry),
                "size_bytes": None,
                "modified_at": None,
            }
        )
    return data


def build_raw_ingest_status(vault: Path, config: dict[str, str], manifest: dict[str, object]) -> dict[str, Any]:
    root = raw_status_dir(vault, config)
    exists = root.exists() and root.is_dir()
    pending = scan_raw_pending(root, vault) if exists else []
    return {
        "raw_dir": str(root),
        "exists": exists,
        "pending_count": len(pending),
        "in_progress": raw_ingest_in_progress(vault),
        "next_file": raw_file_to_json(pending[0], vault) if pending else None,
        "last_ingest": latest_raw_ingest(manifest, vault, root),
    }


def raw_status_dir(vault: Path, config: dict[str, str]) -> Path:
    raw = config.get("OBSIDIAN_RAW_DIR") or "_raw"
    path = Path(raw).expanduser()
    return path if path.is_absolute() else vault / path


def scan_raw_pending(root: Path, vault: Path) -> list[SourceFile]:
    pending: list[SourceFile] = []
    for path in root.rglob("*"):
        if path.is_file() and is_supported_status_source(path):
            pending.append(raw_source_file(path, vault))
    return sorted(pending, key=lambda source: relative_path_text(source.path, vault))


def raw_source_file(path: Path, vault: Path) -> SourceFile:
    resolved = path.expanduser().resolve()
    stat = resolved.stat()
    return SourceFile(
        path=resolved,
        display=relative_path_text(resolved, vault),
        size_bytes=stat.st_size,
        modified_at=datetime.fromtimestamp(stat.st_mtime, timezone.utc),
        source_type=source_type_for_path(resolved),
    )


def raw_file_to_json(source: SourceFile, vault: Path) -> dict[str, Any]:
    return {
        "relpath": relative_path_text(source.path, vault),
        "name": source.path.name,
        "size_bytes": source.size_bytes,
        "mtime": status_datetime(source.modified_at),
    }


def raw_ingest_in_progress(vault: Path) -> bool:
    root = vault / "_runs"
    if not root.exists():
        return False
    for run_dir in root.iterdir():
        if not run_dir.is_dir():
            continue
        packet_path = run_dir / "packet.json"
        if not packet_path.exists() or (run_dir / "plan.json").exists():
            continue
        try:
            packet = json.loads(packet_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if isinstance(packet, dict) and packet.get("mode") == "raw":
            return True
    return False


def latest_raw_ingest(manifest: dict[str, object], vault: Path, raw_root: Path) -> dict[str, Any] | None:
    source_entries = manifest.get("sources", {})
    if not isinstance(source_entries, dict):
        return None
    raw_resolved = raw_root.expanduser().resolve(strict=False)
    candidates: list[tuple[datetime, str, str]] = []
    for key, entry in source_entries.items():
        if not isinstance(key, str) or not is_path_source(key) or not isinstance(entry, dict):
            continue
        source_path = Path(key).expanduser().resolve(strict=False)
        if not is_relative_to_path(source_path, raw_resolved):
            continue
        ingested_at = str(entry.get("ingested_at") or "")
        parsed = parse_datetime(ingested_at)
        if parsed is None:
            continue
        candidates.append((parsed, relative_path_text(source_path, vault), "ok"))
    if not candidates:
        return None
    finished_at, relpath, status = sorted(candidates, key=lambda item: item[0])[-1]
    return {
        "finished_at": status_datetime(finished_at),
        "source_relpath": relpath,
        "status": status,
    }


def build_status_jobs(raw_ingest: dict[str, Any]) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    pending_count = int(raw_ingest.get("pending_count") or 0)
    in_progress = bool(raw_ingest.get("in_progress"))
    if pending_count or in_progress:
        runnable = pending_count > 0 and not in_progress
        items.append(
            {
                "id": "raw-ingest",
                "kind": "raw_ingest",
                "command": ["a-inf", "ingest", "--once", "--raw"],
                "runnable": runnable,
                "reason": "raw_ingest_in_progress" if in_progress else "raw_file_pending",
            }
        )
    return {
        "runnable": sum(1 for item in items if item["runnable"]),
        "queued": len(items),
        "blocked": 0,
        "items": items,
    }


def render_status_delta_items(items: list[dict[str, Any]], fields: list[str], limit: int = 10) -> str:
    if not items:
        return "_None._"
    lines: list[str] = []
    for item in items[:limit]:
        source = str(item.get(fields[0]) or item.get("manifest_key") or "-")
        details = []
        for field in fields[1:]:
            value = item.get(field)
            if field == "size_bytes" and isinstance(value, int):
                value = format_bytes(value)
            details.append(str(value or "-"))
        suffix = f" - {', '.join(details)}" if details else ""
        lines.append(f"- {source}{suffix}")
    if len(items) > limit:
        lines.append(f"_Showing {limit} of {len(items)}._")
    return "\n".join(lines)


def relative_path_text(path: Path, vault: Path) -> str:
    try:
        return path.relative_to(vault).as_posix()
    except ValueError:
        return str(path)


def status_datetime(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def is_relative_to_path(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


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
            if path.is_file() and is_supported_status_source(path):
                add_source_file(sources, path, source_type_for_path(path))

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


def is_supported_status_source(path: Path) -> bool:
    return path.suffix.lower() in SUPPORTED_SOURCE_SUFFIXES


def source_type_for_path(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in IMAGE_SUFFIXES:
        return "image"
    if suffix in PDF_SUFFIXES:
        return "pdf"
    return "document"


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
    dest.mkdir(parents=True, exist_ok=True)
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


def has_a_inf_structure(vault: Path) -> bool:
    return (vault / ".a-inf").is_dir()


def write_file_if_missing(path: Path, content: str) -> None:
    if not path.exists():
        path.write_text(content, encoding="utf-8")


def write_bytes_if_missing(path: Path, content: bytes) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_bytes(content)
        return True
    return False


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
    required_entries = [
        ".DS_Store",
        "_raw/",
        "_sources/",
        "_runs/",
        ".env",
        ".a-inf/",
        ".obsidian/workspace.json",
        ".obsidian/plugins",
        "graph.json.backup-*",
    ]
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
tags: {managed_tags()}
---

# Wiki Index

*This index is automatically maintained. Last updated: {now_iso()}*

## Concepts

*No pages yet. Use `a-inf ingest <source>` to add your first source.*

## Entities

## References

## Synthesis
"""


def log_template(vault: Path) -> str:
    return f"""---
title: Wiki Log
tags: {managed_tags()}
---

# Wiki Log

- [{now_iso()}] INIT vault_path="{vault}" categories=concepts,entities,references,synthesis,projects
"""


def hot_template(vault: Path) -> str:
    return f"""---
title: Hot Cache
tags: {managed_tags()}
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
tags: {managed_tags(["taxonomy"])}
sources: []
created: {now_iso()}
updated: {now_iso()}
---

# Tag Taxonomy

Canonical tags will be added here as the vault grows.
"""


def graph_template() -> dict[str, object]:
    return {
        "collapse-filter": True,
        "search": "-tag:#a-inf -path:_sources",
        "showTags": False,
        "showAttachments": False,
        "hideUnresolved": False,
        "showOrphans": True,
        "collapse-color-groups": True,
        "colorGroups": [
            {"query": 'path:"concepts"', "color": {"a": 1, "rgb": 5142951}},
            {"query": 'path:"references"', "color": {"a": 1, "rgb": 7780274}},
        ],
        "collapse-display": True,
        "showArrow": False,
        "textFadeMultiplier": 0,
        "nodeSizeMultiplier": 1,
        "lineSizeMultiplier": 1,
        "collapse-forces": True,
        "centerStrength": 0.518713248970312,
        "repelStrength": 10,
        "linkStrength": 1,
        "linkDistance": 250,
        "scale": 1,
        "close": False,
    }


def env_template(vault: Path) -> str:
    return f"""OBSIDIAN_VAULT_PATH={vault}
OBSIDIAN_SOURCES_DIR=_raw
OBSIDIAN_CATEGORIES=concepts,entities,references,synthesis,projects
OBSIDIAN_MAX_PAGES_PER_INGEST=15
CODEX_HISTORY_PATH=
LINT_SCHEDULE=weekly
OBSIDIAN_LINK_FORMAT=wikilink
OBSIDIAN_RAW_DIR=_raw
QMD_WIKI_COLLECTION={vault.name}
QMD_PAPERS_COLLECTION={vault.name}
A_INF_ARCHIVE_SOURCES=true
A_INF_SOURCE_ARCHIVE_DIR=_sources
A_INF_QUERY_SOURCE_DETAIL=auto
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
- Local skill instructions are symlinked under `.agents/skills/<name>/SKILL.md`.
- The CLI may dispatch complex workflows to Codex; when it does, follow the selected skill file exactly.
- Keep `.manifest.json`, `index.md`, `log.md`, and `hot.md` current after write operations.
- Use `[[wikilinks]]` unless local config sets `OBSIDIAN_LINK_FORMAT=markdown`.
- For generated Markdown math, use `$...$` inline and `$$...$$` display delimiters; do not use `\\[` or `\\]`.
<!-- END A-INF -->
"""


if __name__ == "__main__":
    raise SystemExit(main())
