from __future__ import annotations

from pathlib import Path


A_INF_TAG = "a-inf"

MANAGED_FILE_TITLES = {
    "index.md": "Wiki Index",
    "log.md": "Wiki Log",
    "hot.md": "Hot Cache",
    "_meta/taxonomy.md": "Tag Taxonomy",
    "AGENTS.md": "Repository Instructions",
}


def render_tags(tags: list[str]) -> str:
    deduped: list[str] = []
    for tag in tags:
        if tag and tag not in deduped:
            deduped.append(tag)
    return "[" + ", ".join(deduped) + "]"


def managed_tags(extra: list[str] | None = None) -> str:
    return render_tags([*(extra or []), A_INF_TAG])


def ensure_vault_managed_tags(vault: Path, *, include_agents: bool = True) -> None:
    for rel, title in MANAGED_FILE_TITLES.items():
        if rel == "AGENTS.md" and not include_agents:
            continue
        ensure_managed_tag(vault / rel, title)


def ensure_managed_tag(path: Path, title: str) -> None:
    if not path.exists() or not path.is_file():
        return
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    if lines and lines[0].strip() == "---":
        end = next((idx for idx, line in enumerate(lines[1:], start=1) if line.strip() == "---"), None)
        if end is not None:
            block = lines[1:end]
            updated = add_tag_to_frontmatter(block, A_INF_TAG)
            if updated != block:
                rewritten = ["---", *updated, "---", *lines[end + 1 :]]
                path.write_text("\n".join(rewritten).rstrip() + "\n", encoding="utf-8")
            return

    header = ["---", f"title: {title}", f"tags: {managed_tags()}", "---", ""]
    path.write_text("\n".join(header) + text.lstrip(), encoding="utf-8")


def add_tag_to_frontmatter(block: list[str], tag: str) -> list[str]:
    updated = list(block)
    for idx, line in enumerate(updated):
        stripped = line.strip()
        if not stripped.startswith("tags:"):
            continue
        value = stripped[len("tags:") :].strip()
        if value:
            tags = parse_tag_items(value)
            if tag in tags:
                return updated
            updated[idx] = f"tags: {render_tags([*tags, tag])}"
            return updated

        end = idx + 1
        tags: list[str] = []
        while end < len(updated) and (updated[end].startswith(" ") or updated[end].lstrip().startswith("-")):
            item = updated[end].strip()
            if item.startswith("-"):
                item = item[1:].strip()
            tags.extend(parse_tag_items(item))
            end += 1
        if tag in tags:
            return updated
        updated.insert(end, f"  - {tag}")
        return updated

    insert_at = next((idx + 1 for idx, line in enumerate(updated) if line.strip().startswith("title:")), 0)
    updated.insert(insert_at, f"tags: {managed_tags()}")
    return updated


def parse_tag_items(value: str) -> list[str]:
    value = value.strip()
    if not value:
        return []
    if value.startswith("[") and value.endswith("]"):
        value = value[1:-1]
    items: list[str] = []
    for part in value.split(","):
        tag = part.strip().strip('"').strip("'")
        if tag and tag not in items:
            items.append(tag)
    return items
