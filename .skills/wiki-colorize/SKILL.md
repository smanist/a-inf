---
name: wiki-colorize
description: >
  Deterministically color-code the Obsidian graph view through `a-inf colorize`.
  Use this skill when the user says "color my graph", "color code obsidian",
  "colorize the graph", "color the graph by tag", "color by category",
  "highlight visibility in graph", "make the graph colorful", "distinguish tags
  in graph", or wants nodes in Obsidian's graph view tinted by tag, folder,
  visibility, or explicit JSON color groups. The CLI generates a stable
  `colorGroups` array from actual vault metadata, merges it into graph.json
  without clobbering unrelated graph settings, and backs up first.
---

# Wiki Colorize — Deterministic Obsidian Graph Colors

Prefer the deterministic CLI. Do not edit `.obsidian/graph.json` by hand when `a-inf colorize` can perform the requested operation.

Obsidian stores graph settings in `<vault>/.obsidian/graph.json`. The `colorGroups` array is a list of `{query, color}` pairs; the first matching query wins per node. Queries use Obsidian's search syntax: `tag:#foo`, `path:"concepts"`, `file:foo`, etc. Color is `{"a": 1, "rgb": <packed-int>}` where the int is `(R << 16) | (G << 8) | B`.

## Before You Start

Run `a-inf colorize` from the vault/repo. The CLI resolves config from `.a-inf/config.toml`, `~/.obsidian-wiki/config`, or `.env`, requires `.obsidian/` to exist, and prints a warning to reload Obsidian after the write.

## Step 1: Pick a Mode

Use deterministic modes. If ambiguous, default to **by-tag**.

| User intent | Mode |
|---|---|
| "color by tag", "color my graph", "make it colorful" | `a-inf colorize --mode by-tag` |
| "color by folder", "color by category", "color by directory" | `a-inf colorize --mode by-category` |
| "highlight visibility", "show internal/pii in graph", "visibility colors" | `a-inf colorize --mode by-visibility` |
| "combine tag and visibility" / "both" | `a-inf colorize --mode combined` |
| User provides explicit JSON mapping | `a-inf colorize --mode custom --groups-json '<json>'` |
| "clear colors" | `a-inf colorize --mode clear` |
| "undo colorize" / "restore graph colors" | `a-inf colorize --mode undo` |

Simple positional compatibility also works: no args or `tag/tags` means `by-tag`; `folder/folders/category/categories` means `by-category`; `visibility`, `combined/both`, `clear`, and `undo/restore` map to their matching modes.

## Step 2: Build the `colorGroups` Array

The Python engine owns group construction. These rules document the expected deterministic behavior.

### Palette

The built-in named palette maps color strings to hex. Config overrides can be added under `[graph_colorize.palette]` in `.a-inf/config.toml`.

| # | Hex | rgb (packed int) | Role |
|---|---|---|---|
| 0 | `#4E79A7` | `5142951` | blue |
| 1 | `#F28E2B` | `15896107` | orange |
| 2 | `#E15759` | `14767961` | red |
| 3 | `#76B7B2` | `7780786` | teal |
| 4 | `#59A14F` | `5873999` | green |
| 5 | `#EDC948` | `15583048` | yellow |
| 6 | `#B07AA1` | `11565217` | purple |
| 7 | `#FF9DA7` | `16751527` | pink |
| 8 | `#9C755F` | `10253663` | brown |
| 9 | `#BAB0AC` | `12234924` | gray |

Every color is wrapped as `{"a": 1, "rgb": <int>}`.

Example override:

```toml
[graph_colorize.palette]
accent = "#3366FF"
blue = "#000001"
```

### Mode: `by-tag`

The CLI reads the page registry, drops `visibility/*` tags, ranks by `(-count, tag)`, takes the top 10, and emits `tag:#<tag>` groups using the palette order.

### Mode: `by-category`

The CLI uses the seven vault top-level folders in this fixed order so colors are stable across runs:

| Folder | Color index |
|---|---|
| `concepts` | 0 (blue) |
| `entities` | 1 (orange) |
| `skills` | 2 (red) |
| `references` | 3 (teal) |
| `synthesis` | 4 (green) |
| `projects` | 5 (yellow) |
| `journal` | 6 (purple) |

It emits one entry per folder that exists and contains at least one `.md` file:

```json
{"query": "path:\"<folder>\"", "color": {"a": 1, "rgb": <int>}}
```

### Mode: `by-visibility`

Emit exactly three entries, in this order (first-match wins, so most restrictive comes first):

1. `visibility/pii` → `#E15759` (red, rgb 14767961)
2. `visibility/internal` → `#F28E2B` (orange, rgb 15896107)
3. `visibility/public` → `#59A14F` (green, rgb 5873999)

```json
{"query": "tag:#visibility/pii", "color": {"a": 1, "rgb": 14767961}}
```

Pages with no `visibility/` tag remain Obsidian's default color — do not add a catch-all.

### Mode: `combined`

Emit `by-visibility` entries first, then `by-tag` entries. Visibility wins on conflict because it appears first in the list.

### Mode: `custom`

Custom mode accepts strict JSON only. Colors can be named palette keys or `#RRGGBB`.

```bash
a-inf colorize --mode custom --groups-json '{"tag:#ml":"blue","path:\"concepts\"":"#3366FF"}'
a-inf colorize --mode custom --groups-json '[{"query":"tag:#ml","color":"orange"}]'
```

## Step 3: Merge into graph.json (Do Not Clobber)

The CLI requires `.obsidian/` to exist. If `graph.json` is missing, it starts from the built-in graph template. If `graph.json` exists, it backs up to `.obsidian/graph.json.backup-<YYYYMMDD-HHMM>` before writing and reuses the same-minute backup if present. It replaces only `colorGroups`, preserves unrelated settings, and writes stable indented JSON.

## Step 4: Report and Log

Print a summary like:

```
Graph colorized -> .obsidian/graph.json
  Mode:    by-tag
  Groups:  7 color assignments
  Backup:  .obsidian/graph.json.backup-20260424-1432

Reload Obsidian (Cmd/Ctrl+R) to see the new colors.
If Obsidian is currently open, close it first OR reload immediately — Obsidian
overwrites graph.json on close and can erase these changes.
```

Append to `$VAULT_PATH/log.md`:

```
- [TIMESTAMP] GRAPH_COLORIZE mode=<mode> groups=<N> backup=graph.json.backup-<stamp>
```

## Edge Cases

- **User wants to undo** → restore from the latest `graph.json.backup-*` and note that in `log.md`.
- **User wants to clear all color groups** → set `colorGroups: []`, back up, log as `GRAPH_COLORIZE mode=clear`.
- **`.obsidian/` missing** → the vault hasn't been opened in Obsidian yet. Tell the user to open it once, then re-run. Don't create `.obsidian/` yourself — Obsidian populates many files there on first open.
- **Query syntax gotchas**: folder paths with spaces need quoting (`path:"my folder"`); tags with nested slashes work literally (`tag:#visibility/internal`); don't URL-encode.
- **Obsidian open during edit**: surface the risk — Obsidian reads graph.json at startup and **rewrites it on close**. If the user is editing live, tell them to close Obsidian first or run the reload (Cmd/Ctrl+R) immediately and avoid opening graph settings before they do.

## Notes

- This is a pure config edit — no page content changes, no frontmatter writes.
- Re-running is safe: each write backs up first, only `colorGroups` is rewritten.
- If the user has manually curated color groups they want to keep, use `custom` mode or `undo` after reviewing backups.
- The palette here matches `wiki-export`'s `graph.html` community colors, so the Obsidian graph and the exported visualization look consistent.
