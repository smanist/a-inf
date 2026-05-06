---
name: wiki-dashboard
description: >
  Create dynamic, queryable dashboard views of the Obsidian vault using Obsidian Bases — a native
  Obsidian feature that turns vault frontmatter into interactive tables, card galleries, and lists.
  Use this skill when the user says "create a dashboard", "vault dashboard", "show all X as a table",
  "dynamic view", "query my vault", "build a content index", "show me all concepts/entities/projects",
  or wants a structured, auto-updating view of their wiki content.
  Requires Obsidian 1.8+ (Bases is a core plugin, no external install needed).
---

# Wiki Dashboard — Dynamic Vault Views

You are creating a `.base` file — an Obsidian Bases definition that turns vault frontmatter into a live, queryable view. The `.base` format is native to Obsidian 1.8+ and requires no plugins.

## Before You Start

1. Read `~/.obsidian-wiki/config` (preferred) or `.env` (fallback) to get `OBSIDIAN_VAULT_PATH`
2. Read `$OBSIDIAN_VAULT_PATH/index.md` to understand what categories and pages exist
3. Ask the user what they want to view if not specified — what folder, tag, category, or date range?

## What Obsidian Bases Can Do

`.base` files define database-style views over vault notes. Each file declares:
- **Which notes to include** — filtered by folder, tag, frontmatter property, or combination
- **Reusable formulas** — computed properties under top-level `formulas`
- **Property display names** — under top-level `properties`
- **One or more views** — `table`, `cards`, or `list`, each with an `order` list of displayed properties
- **Optional grouping and summaries** — using the current Obsidian Bases `views` schema

Embed a `.base` into any note with `![[MyBase.base]]`.

## Step 1: Understand the Request

Determine:
- **What to show** — all pages in a category? Pages with a specific tag? A project's pages?
- **What properties matter** — title, tags, created, updated, summary, category, project?
- **View type** — table (default), cards (visual), or list (minimal)
- **Any filters** — date range, specific tags, folder scope

## Step 2: Generate the `.base` File

The `.base` format is YAML. Use the current Obsidian Bases schema: top-level `filters`, `formulas`, `properties`, `summaries`, and `views`. Do **not** use legacy top-level `columns` or `sort` keys.

### Basic table — all pages in a category folder
```yaml
filters:
  and:
    - 'file.inFolder("concepts")'
properties:
  file.name:
    displayName: Page
  tags:
    displayName: Tags
  summary:
    displayName: Summary
  updated:
    displayName: Updated
views:
  - type: table
    name: "Concepts"
    order:
      - file.name
      - tags
      - summary
      - updated
```

### Filtered by tag
```yaml
filters:
  and:
    - 'file.hasTag("machine-learning")'
properties:
  file.name:
    displayName: Page
  category:
    displayName: Category
  summary:
    displayName: Summary
  created:
    displayName: Created
views:
  - type: table
    name: "Machine Learning"
    order:
      - file.name
      - category
      - summary
      - created
```

### Multi-filter (folder AND tag)
```yaml
filters:
  and:
    - 'file.inFolder("projects")'
    - 'file.hasTag("active")'
properties:
  file.name:
    displayName: Project
  summary:
    displayName: Summary
  updated:
    displayName: Last Updated
views:
  - type: cards
    name: "Active Projects"
    order:
      - file.name
      - summary
      - updated
```

### Computed column (days since last update)
```yaml
formulas:
  days_stale: 'if(updated, (today() - date(updated)).days, (today() - file.mtime).days)'
properties:
  file.name:
    displayName: Page
  updated:
    displayName: Updated
  formula.days_stale:
    displayName: Days Stale
views:
  - type: table
    name: "Stale Pages"
    order:
      - file.name
      - updated
      - formula.days_stale
```

### Filter operators and functions available
- `file.hasTag("tag")` — boolean, true if page has tag
- `file.inFolder("path")` — boolean, true if page is in folder
- `file.name` — the note's filename (without extension)
- `file.path` — full vault-relative path
- `now()` — current date/time
- `today()` — current date
- Arithmetic: `+`, `-`, `*`, `/`, `floor()`, `ceil()`
- Comparison: `==`, `!=`, `>`, `<`, `>=`, `<=`

## Step 3: Write the File

Target path: `$OBSIDIAN_VAULT_PATH/_meta/<dashboard-name>.base`

Use a slug derived from the dashboard's purpose:
- "All concepts" → `_meta/concepts-index.base`
- "Recent ingests" → `_meta/recent-ingests.base`
- "Project overview" → `_meta/projects-overview.base`
- "Stale pages" → `_meta/stale-pages.base`

Create `_meta/` if it doesn't exist yet.

## Step 4: Embed (optional)

If the user wants the dashboard embedded in an existing note (e.g., `index.md` or a project overview), add:

```markdown
## <Dashboard Title>

![[_meta/<dashboard-name>.base]]
```

Ask the user before modifying an existing note.

## Step 5: Update Tracking

**`log.md`** — Append:
```
- [TIMESTAMP] WIKI_DASHBOARD name="<slug>" view=<type> filter="<description>"
```

No manifest or index update needed — `.base` files are live queries, not static content pages.

## Common Dashboard Recipes

Tell the user about these if they're not sure what to ask for:

| Dashboard | What it shows |
|---|---|
| **Content index** | All wiki pages grouped by category, sortable by updated date |
| **Entity tracker** | All entity pages (people, tools, orgs) with tags and sources |
| **Ingestion log** | Pages sorted by `created` date — see what was added recently |
| **Stale content** | Pages not updated in 30+ days — maintenance view |
| **Project overview** | All project pages with last-sync date |
| **Tag cloud** | Pages grouped by tag — see coverage across topics |
| **Research tracker** | All synthesis pages tagged `research` — shows research history |

## Quality Checklist

- [ ] `.base` YAML is valid and uses correct field names
- [ ] Filter matches the user's intent
- [ ] File written to `_meta/` with a descriptive slug
- [ ] `log.md` updated
- [ ] User told how to embed it (`![[_meta/<name>.base]]`) and what Obsidian version is required (1.8+)
