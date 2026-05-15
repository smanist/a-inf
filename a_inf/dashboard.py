from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from a_inf import lint
from a_inf.ingest import load_wiki_config, write_json
from a_inf.managed_files import ensure_managed_tag, managed_tags
from a_inf.qmd import ensure_qmd_collection, qmd_env, sync_qmd
from a_inf.runs import timestamped_run_dir


VALID_RECIPES = {
    "content-index",
    "entities",
    "recent-ingests",
    "stale-pages",
    "projects",
    "tag-cloud",
    "research",
}
VALID_VIEWS = {"table", "cards", "list"}
CONTENT_FOLDERS = ["concepts", "entities", "skills", "references", "synthesis", "journal", "projects"]
ALLOWED_BASE_KEYS = {"filters", "formulas", "properties", "summaries", "views"}
ALLOWED_VIEW_KEYS = {"type", "name", "limit", "groupBy", "filters", "order", "summaries"}
PROP_RE = re.compile(r"^(?:file|formula|note)\.[A-Za-z_][A-Za-z0-9_.-]*$|^[A-Za-z_][A-Za-z0-9_.-]*$")
FORMULA_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class DashboardError(Exception):
    pass


@dataclass(frozen=True)
class Run:
    run_dir: Path
    packet_path: Path
    spec_path: Path
    report_path: Path


def run_dashboard(args: Any, vault: Path, config: dict[str, str] | None = None) -> int:
    config = config or load_wiki_config(vault)
    try:
        prompt = prompt_for_request(args, vault, config)
        if prompt is not None:
            print(prompt)
            return 0
        spec, run = resolve_spec(args, vault, config)
        validated = validate_spec(spec, vault)
        yaml_text = render_base_yaml(validated["base"])
        report = build_report(validated, yaml_text, status="planned" if getattr(args, "dry_run", False) else "completed")

        if getattr(args, "dry_run", False):
            print_report(report, json_output=getattr(args, "json", False))
            return 0

        output_path = vault / validated["path"]
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(yaml_text, encoding="utf-8")

        logged = False
        warnings: list[str] = []
        if not getattr(args, "no_log", False):
            append_log(vault, validated)
            logged = True
        if getattr(args, "sandbox", "workspace-write") != "read-only":
            if ensure_qmd_collection(vault, config):
                if not sync_qmd(vault, config):
                    warnings.append("QMD sync failed after dashboard; wiki files were still updated.")
            else:
                warnings.append("QMD sync skipped or failed after dashboard; wiki files were still updated.")

        report["logged"] = logged
        report["warnings"] = warnings
        if run is not None:
            write_json(run.report_path, report)
        print_report(report, json_output=getattr(args, "json", False))
        return 0
    except DashboardError as exc:
        report = {"status": "error", "error": str(exc)}
        if getattr(args, "json", False):
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print(f"Dashboard failed: {exc}", file=sys.stderr)
        return 1


def prompt_for_request(args: Any, vault: Path, config: dict[str, str]) -> str | None:
    if not getattr(args, "print_prompt", False):
        return None
    recipe = infer_recipe(getattr(args, "recipe", None), list(getattr(args, "args", [])))
    if recipe is not None or has_explicit_spec_args(args):
        spec = build_recipe_spec(
            recipe or "content-index",
            view=getattr(args, "view", None) or "table",
            name=getattr(args, "name", None),
            title=getattr(args, "title", None),
            folder=getattr(args, "folder", None),
            tag=getattr(args, "tag", None),
            limit=getattr(args, "limit", None),
        )
        return render_base_yaml(validate_spec(spec, vault)["base"])

    run = create_run(vault)
    packet = build_packet(args, vault, config, run.spec_path)
    write_json(run.packet_path, packet)
    return build_prompt(vault, run.packet_path, run.spec_path)


def resolve_spec(args: Any, vault: Path, config: dict[str, str]) -> tuple[dict[str, Any], Run | None]:
    recipe = infer_recipe(getattr(args, "recipe", None), list(getattr(args, "args", [])))
    if recipe is not None or has_explicit_spec_args(args):
        spec = build_recipe_spec(
            recipe or "content-index",
            view=getattr(args, "view", None) or "table",
            name=getattr(args, "name", None),
            title=getattr(args, "title", None),
            folder=getattr(args, "folder", None),
            tag=getattr(args, "tag", None),
            limit=getattr(args, "limit", None),
        )
        return spec, None

    run = create_run(vault)
    packet = build_packet(args, vault, config, run.spec_path)
    write_json(run.packet_path, packet)
    prompt = build_prompt(vault, run.packet_path, run.spec_path)

    if getattr(args, "no_codex", False):
        raise DashboardError("dashboard request is ambiguous; re-run without --no-codex, use --print-prompt, or pass --recipe/--folder/--tag")

    codex_bin = shutil.which(getattr(args, "codex_bin", "codex"))
    if codex_bin is None:
        print("Codex executable not found. Re-run with --print-prompt or install Codex CLI.", file=sys.stderr)
        print(prompt)
        raise DashboardError("Codex executable not found")

    command = [
        codex_bin,
        "exec",
        "--sandbox",
        getattr(args, "sandbox", "workspace-write"),
        "--cd",
        str(vault),
        "--add-dir",
        str(run.run_dir.resolve()),
        prompt,
    ]
    for directory in getattr(args, "add_dir", []) or []:
        command[-1:-1] = ["--add-dir", str(Path(directory).expanduser().resolve())]
    result = subprocess.call(command, cwd=vault, env=qmd_env(os.environ, vault))
    if result != 0:
        raise DashboardError(f"Codex dashboard planning failed with exit code {result}")
    return read_spec(run.spec_path), run


def infer_recipe(explicit: str | None, args: list[str]) -> str | None:
    if explicit:
        recipe = normalize_recipe(explicit)
        if recipe not in VALID_RECIPES:
            raise DashboardError(f"unknown dashboard recipe: {explicit}")
        return recipe
    text = " ".join(args).strip().lower()
    if not text:
        return "content-index"
    recipe = normalize_recipe(text)
    aliases = {
        "content": "content-index",
        "index": "content-index",
        "content-index": "content-index",
        "all": "content-index",
        "entities": "entities",
        "entity-tracker": "entities",
        "recent": "recent-ingests",
        "recent-ingests": "recent-ingests",
        "ingestion-log": "recent-ingests",
        "stale": "stale-pages",
        "stale-pages": "stale-pages",
        "projects": "projects",
        "project-overview": "projects",
        "tag-cloud": "tag-cloud",
        "tags": "tag-cloud",
        "research": "research",
        "research-tracker": "research",
    }
    return aliases.get(recipe)


def normalize_recipe(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")


def has_explicit_spec_args(args: Any) -> bool:
    return any(getattr(args, name, None) for name in ["folder", "tag", "view", "name", "title", "limit"])


def build_recipe_spec(
    recipe: str,
    *,
    view: str,
    name: str | None,
    title: str | None,
    folder: str | None,
    tag: str | None,
    limit: int | None,
) -> dict[str, Any]:
    spec = recipe_spec(recipe, view)
    if folder:
        append_filter(spec["base"], folder_filter(folder))
        spec["filter_description"] = f'{spec["filter_description"]}; folder="{folder}"'
    if tag:
        append_filter(spec["base"], tag_filter(tag))
        spec["filter_description"] = f'{spec["filter_description"]}; tag="#{tag.lstrip("#")}"'
    if title:
        spec["title"] = title
        spec["base"]["views"][0]["name"] = title
    if name:
        spec["name"] = slugify(name)
        spec["path"] = f"_meta/{spec['name']}.base"
    if limit is not None:
        spec["base"]["views"][0]["limit"] = limit
    return spec


def recipe_spec(recipe: str, view: str) -> dict[str, Any]:
    common_properties = {
        "file.name": {"displayName": "Page"},
        "category": {"displayName": "Category"},
        "tags": {"displayName": "Tags"},
        "summary": {"displayName": "Summary"},
        "updated": {"displayName": "Updated"},
        "created": {"displayName": "Created"},
    }
    if recipe == "content-index":
        return spec_for(
            "content-index",
            "Content Index",
            view,
            content_filter(),
            ["file.name", "category", "tags", "summary", "updated"],
            common_properties,
            "content folders",
        )
    if recipe == "entities":
        return spec_for(
            "entities-index",
            "Entity Tracker",
            view,
            {"and": [folder_filter("entities")]},
            ["file.name", "tags", "summary", "sources", "updated"],
            {**common_properties, "sources": {"displayName": "Sources"}},
            "folder=\"entities\"",
        )
    if recipe == "recent-ingests":
        return spec_for(
            "recent-ingests",
            "Recent Ingests",
            view,
            content_filter(),
            ["file.name", "created", "updated", "category", "sources"],
            {**common_properties, "sources": {"displayName": "Sources"}},
            "content folders by created/updated metadata",
        )
    if recipe == "stale-pages":
        return spec_for(
            "stale-pages",
            "Stale Pages",
            view,
            {"and": [*content_filter()["and"], "formula.days_stale >= 30"]},
            ["file.name", "updated", "formula.days_stale", "category", "summary"],
            {**common_properties, "formula.days_stale": {"displayName": "Days Stale"}},
            "content folders with formula.days_stale >= 30",
            formulas={"days_stale": 'if(updated, (today() - date(updated)).days, (today() - file.mtime).days)'},
        )
    if recipe == "projects":
        return spec_for(
            "projects-overview",
            "Project Overview",
            view,
            {"and": [folder_filter("projects")]},
            ["file.name", "summary", "updated", "tags", "sources"],
            {**common_properties, "sources": {"displayName": "Sources"}},
            "folder=\"projects\"",
        )
    if recipe == "tag-cloud":
        spec = spec_for(
            "tag-cloud",
            "Tag Cloud",
            view,
            content_filter(),
            ["file.name", "file.tags", "category", "summary"],
            {**common_properties, "file.tags": {"displayName": "Tags"}},
            "content folders grouped by file.tags",
        )
        spec["base"]["views"][0]["groupBy"] = {"property": "file.tags", "direction": "ASC"}
        return spec
    if recipe == "research":
        return spec_for(
            "research-tracker",
            "Research Tracker",
            view,
            {"and": [folder_filter("synthesis"), tag_filter("research")]},
            ["file.name", "summary", "updated", "tags", "sources"],
            {**common_properties, "sources": {"displayName": "Sources"}},
            'folder="synthesis"; tag="#research"',
        )
    raise DashboardError(f"unknown dashboard recipe: {recipe}")


def spec_for(
    name: str,
    title: str,
    view: str,
    filters: Any,
    order: list[str],
    properties: dict[str, Any],
    filter_description: str,
    *,
    formulas: dict[str, str] | None = None,
) -> dict[str, Any]:
    base: dict[str, Any] = {
        "filters": filters,
        "properties": properties,
        "views": [{"type": view, "name": title, "order": order}],
    }
    if formulas:
        base["formulas"] = formulas
    return {
        "version": 1,
        "name": name,
        "title": title,
        "path": f"_meta/{name}.base",
        "filter_description": filter_description,
        "base": base,
    }


def content_filter() -> dict[str, Any]:
    return {
        "and": [
            'file.ext == "md"',
            {"or": [folder_filter(folder) for folder in CONTENT_FOLDERS]},
        ]
    }


def folder_filter(folder: str) -> str:
    clean = folder.strip().strip("/")
    if not clean or clean.startswith(".") or ".." in clean.split("/"):
        raise DashboardError(f"invalid folder filter: {folder}")
    return f'file.inFolder("{escape_expression_string(clean)}")'


def tag_filter(tag: str) -> str:
    clean = tag.strip().lstrip("#")
    if not re.match(r"^[A-Za-z0-9][A-Za-z0-9_/-]*$", clean):
        raise DashboardError(f"invalid tag filter: {tag}")
    return f'file.hasTag("{escape_expression_string(clean)}")'


def append_filter(base: dict[str, Any], item: str) -> None:
    current = base.get("filters")
    if isinstance(current, dict) and set(current) == {"and"} and isinstance(current["and"], list):
        current["and"].append(item)
    else:
        base["filters"] = {"and": [current, item]} if current else {"and": [item]}


def validate_spec(spec: dict[str, Any], vault: Path) -> dict[str, Any]:
    if not isinstance(spec, dict):
        raise DashboardError("dashboard spec must be a JSON object")
    base = spec.get("base")
    if not isinstance(base, dict):
        raise DashboardError("dashboard spec must include base object")
    legacy = {"columns", "sort"}.intersection(base)
    if legacy:
        raise DashboardError(f"legacy base keys are not supported: {', '.join(sorted(legacy))}")
    unknown = set(base).difference(ALLOWED_BASE_KEYS)
    if unknown:
        raise DashboardError(f"unsupported base keys: {', '.join(sorted(unknown))}")

    rel = safe_output_path(str(spec.get("path") or f"_meta/{slugify(str(spec.get('name') or 'dashboard'))}.base"), vault)
    name = slugify(str(spec.get("name") or Path(rel).stem))
    title = str(spec.get("title") or name.replace("-", " ").title())
    filter_description = str(spec.get("filter_description") or "custom")

    validated_base: dict[str, Any] = {}
    if "filters" in base:
        validated_base["filters"] = validate_filter(base["filters"])
    if "formulas" in base:
        validated_base["formulas"] = validate_formulas(base["formulas"])
    if "properties" in base:
        validated_base["properties"] = validate_properties(base["properties"])
    if "summaries" in base:
        validated_base["summaries"] = validate_string_map(base["summaries"], "summaries")
    validated_base["views"] = validate_views(base.get("views"))

    return {
        "version": 1,
        "name": name,
        "title": title,
        "path": rel,
        "filter_description": filter_description,
        "base": validated_base,
    }


def safe_output_path(raw: str, vault: Path) -> str:
    rel = Path(raw)
    if rel.is_absolute() or rel.suffix != ".base" or len(rel.parts) != 2 or rel.parts[0] != "_meta":
        raise DashboardError("dashboard path must be _meta/<name>.base")
    if any(part in {"", ".", ".."} for part in rel.parts):
        raise DashboardError("dashboard path must not contain traversal")
    resolved = (vault / rel).resolve(strict=False)
    vault_resolved = vault.resolve(strict=False)
    if not resolved.is_relative_to(vault_resolved):
        raise DashboardError("dashboard path escapes vault")
    return rel.as_posix()


def validate_filter(value: Any) -> Any:
    if isinstance(value, str):
        if not value.strip():
            raise DashboardError("filter strings cannot be empty")
        return value
    if not isinstance(value, dict):
        raise DashboardError("filters must be a string or an and/or/not object")
    keys = set(value)
    if len(keys) != 1 or not keys.issubset({"and", "or", "not"}):
        raise DashboardError("filter objects must contain exactly one of and/or/not")
    key = next(iter(keys))
    items = value[key]
    if not isinstance(items, list) or not items:
        raise DashboardError(f"filter {key} must be a non-empty list")
    return {key: [validate_filter(item) for item in items]}


def validate_formulas(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        raise DashboardError("formulas must be an object")
    formulas: dict[str, str] = {}
    for key, formula in value.items():
        if not FORMULA_KEY_RE.match(str(key)):
            raise DashboardError(f"invalid formula name: {key}")
        if not isinstance(formula, str) or not formula.strip():
            raise DashboardError(f"formula {key} must be a non-empty string")
        formulas[str(key)] = formula
    return formulas


def validate_properties(value: Any) -> dict[str, dict[str, str]]:
    if not isinstance(value, dict):
        raise DashboardError("properties must be an object")
    properties: dict[str, dict[str, str]] = {}
    for prop, config in value.items():
        prop_name = validate_property_name(str(prop))
        if not isinstance(config, dict):
            raise DashboardError(f"property {prop_name} config must be an object")
        display = config.get("displayName")
        if not isinstance(display, str) or not display.strip():
            raise DashboardError(f"property {prop_name} must define displayName")
        properties[prop_name] = {"displayName": display}
    return properties


def validate_views(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise DashboardError("views must be a non-empty list")
    views: list[dict[str, Any]] = []
    for raw in value:
        if not isinstance(raw, dict):
            raise DashboardError("each view must be an object")
        legacy = {"columns", "sort"}.intersection(raw)
        if legacy:
            raise DashboardError(f"legacy view keys are not supported: {', '.join(sorted(legacy))}")
        unknown = set(raw).difference(ALLOWED_VIEW_KEYS)
        if unknown:
            raise DashboardError(f"unsupported view keys: {', '.join(sorted(unknown))}")
        view_type = raw.get("type")
        if view_type not in VALID_VIEWS:
            raise DashboardError(f"invalid view type: {view_type}")
        name = raw.get("name")
        if not isinstance(name, str) or not name.strip():
            raise DashboardError("view name must be a non-empty string")
        order = raw.get("order")
        if not isinstance(order, list) or not order:
            raise DashboardError("view order must be a non-empty list")
        view: dict[str, Any] = {"type": view_type, "name": name}
        if "limit" in raw:
            limit = raw["limit"]
            if not isinstance(limit, int) or limit <= 0:
                raise DashboardError("view limit must be a positive integer")
            view["limit"] = limit
        if "groupBy" in raw:
            view["groupBy"] = validate_group_by(raw["groupBy"])
        if "filters" in raw:
            view["filters"] = validate_filter(raw["filters"])
        view["order"] = [validate_property_name(str(prop)) for prop in order]
        if "summaries" in raw:
            view["summaries"] = validate_string_map(raw["summaries"], "view summaries")
        views.append(view)
    return views


def validate_group_by(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        raise DashboardError("groupBy must be an object")
    prop = validate_property_name(str(value.get("property") or ""))
    direction = str(value.get("direction") or "ASC").upper()
    if direction not in {"ASC", "DESC"}:
        raise DashboardError("groupBy direction must be ASC or DESC")
    return {"property": prop, "direction": direction}


def validate_string_map(value: Any, label: str) -> dict[str, str]:
    if not isinstance(value, dict):
        raise DashboardError(f"{label} must be an object")
    result: dict[str, str] = {}
    for key, item in value.items():
        prop = validate_property_name(str(key))
        if not isinstance(item, str) or not item.strip():
            raise DashboardError(f"{label} value for {prop} must be a non-empty string")
        result[prop] = item
    return result


def validate_property_name(value: str) -> str:
    if not PROP_RE.match(value):
        raise DashboardError(f"invalid property name: {value}")
    return value


def render_base_yaml(base: dict[str, Any]) -> str:
    ordered: dict[str, Any] = {}
    for key in ["filters", "formulas", "properties", "summaries", "views"]:
        if key in base:
            ordered[key] = base[key]
    return dump_yaml(ordered).rstrip() + "\n"


def dump_yaml(value: Any, indent: int = 0) -> str:
    pad = " " * indent
    if isinstance(value, dict):
        lines: list[str] = []
        for key, item in value.items():
            if isinstance(item, (dict, list)):
                lines.append(f"{pad}{key}:")
                lines.append(dump_yaml(item, indent + 2))
            else:
                lines.append(f"{pad}{key}: {yaml_scalar(item)}")
        return "\n".join(lines)
    if isinstance(value, list):
        lines = []
        for item in value:
            if isinstance(item, dict):
                if not item:
                    lines.append(f"{pad}- {{}}")
                    continue
                first = True
                for key, child in item.items():
                    prefix = "- " if first else "  "
                    if isinstance(child, (dict, list)):
                        lines.append(f"{pad}{prefix}{key}:")
                        lines.append(dump_yaml(child, indent + 4))
                    else:
                        lines.append(f"{pad}{prefix}{key}: {yaml_scalar(child)}")
                    first = False
            elif isinstance(item, list):
                lines.append(f"{pad}-")
                lines.append(dump_yaml(item, indent + 2))
            else:
                lines.append(f"{pad}- {yaml_scalar(item)}")
        return "\n".join(lines)
    return f"{pad}{yaml_scalar(value)}"


def yaml_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value)
    text = str(value)
    if '"' in text:
        return "'" + text.replace("'", "''") + "'"
    return json.dumps(text, ensure_ascii=False)


def build_packet(args: Any, vault: Path, config: dict[str, str], spec_path: Path) -> dict[str, Any]:
    pages = lint.build_page_registry(vault)
    folders = Counter(page.category for page in pages.values())
    tags = Counter(tag for page in pages.values() for tag in page.tags if not tag.startswith("visibility/"))
    return {
        "version": 1,
        "generated_at": now_iso(),
        "vault": str(vault),
        "link_format": config.get("OBSIDIAN_LINK_FORMAT") or config.get("link_format") or "wikilink",
        "dashboard_spec_path": str(spec_path),
        "user_request": " ".join(getattr(args, "args", []) or []),
        "valid_recipes": sorted(VALID_RECIPES),
        "valid_view_types": sorted(VALID_VIEWS),
        "folders": dict(sorted(folders.items())),
        "top_tags": dict(tags.most_common(20)),
        "schema": {
            "required": ["version", "name", "title", "path", "filter_description", "base"],
            "path_rule": "_meta/<slug>.base only",
            "base_keys": sorted(ALLOWED_BASE_KEYS),
            "view_keys": sorted(ALLOWED_VIEW_KEYS),
            "forbidden_legacy_keys": ["columns", "sort"],
        },
    }


def build_prompt(vault: Path, packet_path: Path, spec_path: Path) -> str:
    return (
        "Use the `wiki-dashboard` skill only to choose a dashboard specification. "
        "Do not edit vault files directly.\n\n"
        f"Vault path: {vault}\n"
        f"Read this dashboard packet: {packet_path}\n"
        f"Write exactly one JSON object to this path: {spec_path}\n\n"
        "The JSON must include version, name, title, path, filter_description, and base. "
        "The path must be `_meta/<slug>.base`. The base object must follow current Obsidian Bases syntax: "
        "optional filters/formulas/properties/summaries and a non-empty views array with view order. "
        "Do not use legacy columns or sort keys."
    )


def read_spec(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise DashboardError(f"dashboard spec was not written: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise DashboardError(f"dashboard spec is invalid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise DashboardError("dashboard spec must be a JSON object")
    return data


def build_report(spec: dict[str, Any], yaml_text: str, *, status: str) -> dict[str, Any]:
    view = spec["base"]["views"][0]
    return {
        "status": status,
        "name": spec["name"],
        "title": spec["title"],
        "path": spec["path"],
        "view": view["type"],
        "filter_description": spec["filter_description"],
        "yaml": yaml_text,
        "logged": False,
        "warnings": [],
    }


def print_report(report: dict[str, Any], *, json_output: bool) -> None:
    if json_output:
        print(json.dumps(report, indent=2, sort_keys=True))
        return
    print(f"Dashboard {report['status']} -> {report['path']}")
    print(f"  Name:   {report['name']}")
    print(f"  View:   {report['view']}")
    print(f"  Filter: {report['filter_description']}")
    for warning in report.get("warnings", []):
        print(f"  Warning: {warning}")


def append_log(vault: Path, spec: dict[str, Any]) -> None:
    path = vault / "log.md"
    timestamp = now_iso()
    view = spec["base"]["views"][0]["type"]
    line = (
        f'- [{timestamp}] WIKI_DASHBOARD name="{spec["name"]}" '
        f'view={view} filter="{escape_log_value(spec["filter_description"])}"'
    )
    if path.exists():
        ensure_managed_tag(path, "Wiki Log")
        existing = path.read_text(encoding="utf-8").rstrip()
        path.write_text(existing + "\n" + line + "\n", encoding="utf-8")
    else:
        path.write_text(f"---\ntitle: Wiki Log\ntags: {managed_tags()}\n---\n\n# Wiki Log\n\n{line}\n", encoding="utf-8")


def create_run(vault: Path) -> Run:
    candidate = timestamped_run_dir(vault, "dashboard")
    return Run(
        run_dir=candidate,
        packet_path=candidate / "packet.json",
        spec_path=candidate / "dashboard_spec.json",
        report_path=candidate / "report.json",
    )


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or "dashboard"


def escape_expression_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def escape_log_value(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"')


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
