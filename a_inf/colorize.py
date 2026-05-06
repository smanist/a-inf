from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Any
import tomllib

from a_inf import lint
from a_inf.managed_files import ensure_managed_tag, managed_tags
from a_inf.qmd import ensure_qmd_collection, sync_qmd


BUILTIN_PALETTE = {
    "blue": "#4E79A7",
    "orange": "#F28E2B",
    "red": "#E15759",
    "teal": "#76B7B2",
    "green": "#59A14F",
    "yellow": "#EDC948",
    "purple": "#B07AA1",
    "pink": "#FF9DA7",
    "brown": "#9C755F",
    "gray": "#BAB0AC",
}
PALETTE_ORDER = list(BUILTIN_PALETTE)
VALID_MODES = {"by-tag", "by-category", "by-visibility", "combined", "custom", "clear", "undo"}
CATEGORY_COLORS = {
    "concepts": "blue",
    "entities": "orange",
    "skills": "red",
    "references": "teal",
    "synthesis": "green",
    "projects": "yellow",
    "journal": "purple",
}
VISIBILITY_COLORS = [
    ("visibility/pii", "red"),
    ("visibility/internal", "orange"),
    ("visibility/public", "green"),
]
HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


class ColorizeError(Exception):
    pass


def run_colorize(args: Any, vault: Path, config: dict[str, str] | None = None) -> int:
    config = config or {}
    if getattr(args, "print_prompt", False):
        try:
            mode = infer_mode(getattr(args, "mode", None), list(getattr(args, "args", [])))
        except ColorizeError as exc:
            print(f"Graph colorize preview failed: {exc}", file=sys.stderr)
            return 1
        print(
            "\n".join(
                [
                    "Deterministic graph colorize preview",
                    f"Vault path: {vault}",
                    f"Mode: {mode}",
                    f"Will update: {vault / '.obsidian' / 'graph.json'}",
                    "No Codex prompt is used for this command.",
                ]
            )
        )
        return 0
    try:
        report = apply_colorize(args, vault, config)
    except ColorizeError as exc:
        report = {"status": "error", "error": str(exc)}
        if getattr(args, "json", False):
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print(f"Graph colorize failed: {exc}", file=sys.stderr)
        return 1

    if getattr(args, "json", False):
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_report(report))
    return 0


def apply_colorize(args: Any, vault: Path, config: dict[str, str]) -> dict[str, Any]:
    obsidian_dir = vault / ".obsidian"
    if not obsidian_dir.is_dir():
        raise ColorizeError(".obsidian/ does not exist; open this vault in Obsidian once, then re-run")

    mode = infer_mode(getattr(args, "mode", None), list(getattr(args, "args", [])))
    palette = load_palette(vault)
    graph_path = obsidian_dir / "graph.json"

    if mode == "undo":
        backup = restore_latest_backup(graph_path)
        groups = read_color_groups(graph_path)
    else:
        groups = build_color_groups(mode, vault, palette, getattr(args, "groups_json", None))
        graph = read_graph(graph_path)
        backup = backup_graph(graph_path) if graph_path.exists() else None
        graph["colorGroups"] = groups
        graph_path.write_text(json.dumps(graph, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    logged = False
    if not getattr(args, "no_log", False):
        append_log(vault, mode, len(groups), backup.name if backup else "")
        logged = True

    qmd_warning = ""
    if logged and getattr(args, "sandbox", "workspace-write") != "read-only":
        if ensure_qmd_collection(vault, config):
            if not sync_qmd(vault, config):
                qmd_warning = "QMD sync failed after colorize; wiki files were still updated."
        else:
            qmd_warning = "QMD sync skipped or failed after colorize; wiki files were still updated."

    return {
        "status": "completed",
        "mode": mode,
        "groups": len(groups),
        "graph_path": ".obsidian/graph.json",
        "backup": f".obsidian/{backup.name}" if backup else "",
        "logged": logged,
        "qmd_warning": qmd_warning,
        "colorGroups": groups,
    }


def infer_mode(explicit: str | None, args: list[str]) -> str:
    if explicit:
        if explicit not in VALID_MODES:
            raise ColorizeError(f"invalid mode: {explicit}")
        return explicit
    text = " ".join(args).strip().lower()
    if not text or text in {"tag", "tags", "by-tag", "by tags", "by tag"}:
        return "by-tag"
    if text in {
        "folder",
        "folders",
        "category",
        "categories",
        "by-folder",
        "by-category",
        "by folder",
        "by category",
    }:
        return "by-category"
    if text in {"visibility", "by-visibility", "by visibility"}:
        return "by-visibility"
    if text in {"combined", "both", "combine"}:
        return "combined"
    if text == "clear":
        return "clear"
    if text in {"undo", "restore"}:
        return "undo"
    raise ColorizeError(f"could not infer colorize mode from arguments: {text}")


def load_palette(vault: Path) -> dict[str, str]:
    palette = dict(BUILTIN_PALETTE)
    config_path = vault / ".a-inf" / "config.toml"
    if not config_path.exists():
        return palette
    try:
        data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ColorizeError(f"could not read palette config: {exc}") from exc
    graph_config = data.get("graph_colorize")
    overrides = graph_config.get("palette", {}) if isinstance(graph_config, dict) else {}
    if not isinstance(overrides, dict):
        raise ColorizeError("[graph_colorize.palette] must be a TOML table")
    for name, value in overrides.items():
        key = str(name).strip().lower()
        hex_value = str(value).strip()
        if not key:
            raise ColorizeError("palette color names cannot be empty")
        if not HEX_RE.match(hex_value):
            raise ColorizeError(f"palette color {key} must be #RRGGBB")
        palette[key] = hex_value.upper()
    return palette


def build_color_groups(
    mode: str, vault: Path, palette: dict[str, str], groups_json: str | None
) -> list[dict[str, Any]]:
    if mode == "by-tag":
        return tag_groups(vault, palette)
    if mode == "by-category":
        return category_groups(vault, palette)
    if mode == "by-visibility":
        return visibility_groups(palette)
    if mode == "combined":
        return [*visibility_groups(palette), *tag_groups(vault, palette)]
    if mode == "custom":
        return custom_groups(groups_json, palette)
    if mode == "clear":
        return []
    raise ColorizeError(f"mode does not build color groups: {mode}")


def tag_groups(vault: Path, palette: dict[str, str]) -> list[dict[str, Any]]:
    counts: Counter[str] = Counter()
    for page in lint.build_page_registry(vault).values():
        counts.update(tag for tag in page.tags if not tag.startswith("visibility/"))
    tags = [tag for tag, _count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:10]]
    return [color_group(f"tag:#{tag}", PALETTE_ORDER[index], palette) for index, tag in enumerate(tags)]


def category_groups(vault: Path, palette: dict[str, str]) -> list[dict[str, Any]]:
    groups = []
    for folder, color_name in CATEGORY_COLORS.items():
        root = vault / folder
        if root.exists() and any(root.rglob("*.md")):
            groups.append(color_group(f'path:"{folder}"', color_name, palette))
    return groups


def visibility_groups(palette: dict[str, str]) -> list[dict[str, Any]]:
    return [color_group(f"tag:#{tag}", color_name, palette) for tag, color_name in VISIBILITY_COLORS]


def custom_groups(raw: str | None, palette: dict[str, str]) -> list[dict[str, Any]]:
    if not raw:
        raise ColorizeError("--groups-json is required with --mode custom")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ColorizeError(f"invalid --groups-json: {exc}") from exc
    if isinstance(parsed, dict):
        items = [{"query": query, "color": color} for query, color in parsed.items()]
    elif isinstance(parsed, list):
        items = parsed
    else:
        raise ColorizeError("--groups-json must be a JSON object or array")
    groups = []
    for item in items:
        if not isinstance(item, dict):
            raise ColorizeError("custom group entries must be objects")
        query = str(item.get("query") or "").strip()
        color = str(item.get("color") or "").strip()
        if not query:
            raise ColorizeError("custom group query cannot be empty")
        if not color:
            raise ColorizeError(f"custom group {query} has no color")
        groups.append({"query": query, "color": color_value(color, palette)})
    return groups


def color_group(query: str, color_name: str, palette: dict[str, str]) -> dict[str, Any]:
    return {"query": query, "color": color_value(color_name, palette)}


def color_value(color: str, palette: dict[str, str]) -> dict[str, int]:
    raw = color.strip()
    hex_value = raw if raw.startswith("#") else palette.get(raw.lower())
    if not hex_value:
        raise ColorizeError(f"unknown color: {color}")
    if not HEX_RE.match(hex_value):
        raise ColorizeError(f"invalid hex color: {color}")
    return {"a": 1, "rgb": int(hex_value[1:], 16)}


def read_graph(path: Path) -> dict[str, Any]:
    if not path.exists():
        from a_inf.cli import graph_template

        return graph_template()
    try:
        graph = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ColorizeError(f"could not read graph.json: {exc}") from exc
    if not isinstance(graph, dict):
        raise ColorizeError("graph.json must contain a JSON object")
    return graph


def backup_graph(path: Path) -> Path:
    backup = path.with_name(f"graph.json.backup-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M')}")
    if not backup.exists():
        shutil.copy2(path, backup)
    return backup


def restore_latest_backup(graph_path: Path) -> Path:
    backups = sorted(graph_path.parent.glob("graph.json.backup-*"))
    if not backups:
        raise ColorizeError("no graph.json.backup-* files found")
    latest = backups[-1]
    shutil.copy2(latest, graph_path)
    return latest


def read_color_groups(graph_path: Path) -> list[dict[str, Any]]:
    graph = read_graph(graph_path)
    groups = graph.get("colorGroups", [])
    return groups if isinstance(groups, list) else []


def append_log(vault: Path, mode: str, groups: int, backup: str) -> None:
    backup_text = f" backup={backup}" if backup else ""
    line = f"- [{now_iso()}] GRAPH_COLORIZE mode={mode} groups={groups}{backup_text}\n"
    path = vault / "log.md"
    if path.exists():
        ensure_managed_tag(path, "Wiki Log")
        path.write_text(path.read_text(encoding="utf-8").rstrip() + "\n" + line, encoding="utf-8")
    else:
        path.write_text(f"---\ntitle: Wiki Log\ntags: {managed_tags()}\n---\n\n# Wiki Log\n\n" + line, encoding="utf-8")


def render_report(report: dict[str, Any]) -> str:
    lines = [
        "Graph colorized -> .obsidian/graph.json",
        f"  Mode:    {report['mode']}",
        f"  Groups:  {report['groups']} color assignments",
    ]
    if report.get("backup"):
        lines.append(f"  Backup:  {report['backup']}")
    if report.get("qmd_warning"):
        lines.append(f"  Warning: {report['qmd_warning']}")
    lines.extend(
        [
            "",
            "Reload Obsidian (Cmd/Ctrl+R) to see the new colors.",
            "If Obsidian is currently open, close it first or reload immediately; Obsidian can overwrite graph.json on close.",
        ]
    )
    return "\n".join(lines)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
