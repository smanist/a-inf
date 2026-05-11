# Obsidian Wiki — Agent Context

A **CLI-first framework** for building and maintaining an Obsidian knowledge base. The `a-inf` CLI is the user-facing interface; markdown skills in `.skills/` remain the bundled workflow specs, with initialized local Codex symlinks under `.agents/skills/`.

## Configuration

Read config in this order (first found wins):

1. **`.a-inf/config.toml`** — CLI-native repo-local config
2. **`~/.obsidian-wiki/config`** — legacy global config, works from any project directory
3. **`.env`** in the a-inf repo — legacy local fallback

`.a-inf/config.toml` sets `vault_path`, `skills_source`, and `link_format`. Legacy env files set `OBSIDIAN_VAULT_PATH`; the global config can also set `OBSIDIAN_WIKI_REPO`.

After reading config, derive the vault path from `vault_path` or `OBSIDIAN_VAULT_PATH`. Always read `<vault path>/AGENTS.md` if it exists. It contains owner-specific conventions (domain vocabulary, ingest preferences, writing style, project scoping) that override framework defaults for all skills. Apply it for the duration of the session.

## Vault Structure

```
$OBSIDIAN_VAULT_PATH/
├── index.md                # Master index — every page listed, always kept current
├── log.md                  # Chronological activity log (ingests, updates, lints)
├── hot.md                  # Session hot cache — ~500-word semantic snapshot of recent activity
├── .manifest.json          # Tracks every ingested source: path, timestamps, pages produced
├── _sources/               # Local-only source archive: originals, extracted text, figures, metadata
├── _meta/
│   ├── taxonomy.md         # Controlled tag vocabulary
│   └── *.base              # Obsidian Bases dashboard definitions (wiki-dashboard skill)
├── _insights.md            # Graph analysis output (hubs, bridges, dead ends)
├── _raw/                   # Staging area — drop rough notes here, next ingest promotes them
├── concepts/               # Abstract ideas, patterns, mental models
├── entities/               # Concrete things — people, tools, libraries, companies
├── skills/                 # How-to knowledge, techniques, procedures
├── references/             # Source cards and summaries — not full copies of original material
├── synthesis/              # Cross-cutting analysis connecting multiple concepts
├── journal/                # Time-bound entries — daily logs, session notes
├── ideas/                  # Agent handoff idea packets tagged a-inf, ignored by the graph
└── projects/
    └── <project-name>.md   # One page per project synced via wiki-update
```

Every wiki page has required frontmatter: `title`, `category`, `tags`, `sources`, `created`, `updated`. Pages connect via internal links — `[[wikilinks]]` by default, or standard Markdown links when `OBSIDIAN_LINK_FORMAT=markdown` is set in config.

## Skill Routing

Prefer CLI commands for user-facing workflows. Bundled skills live in `.skills/<name>/SKILL.md`; initialized local Codex skills are symlinked at `.agents/skills/<name>/SKILL.md`.

| User says something like… | CLI | Execution |
|---|---|---|
| "set up my wiki" / "initialize" | `a-inf init` | deterministic CLI |
| "import my Codex history" | `a-inf history` | `codex-history-ingest` |
| "add this URL" / "ingest this link" | `a-inf ingest <url>` | `wiki-ingest` |
| "ingest" / "process these docs" | `a-inf ingest <source>` | `wiki-ingest` |
| "process this export" / logs, transcripts | `a-inf ingest <source>` | `wiki-ingest` |
| "what's the status" / "show the delta" | `a-inf status` | deterministic CLI |
| "wiki insights" / "hubs" / "wiki structure" | `a-inf insights` | `wiki-insights` |
| "what do I know about X" / any question | `a-inf query "X"` | `wiki-query` |
| "audit" / "lint" / "find broken links" | `a-inf lint` | `wiki-lint` |
| "rebuild" / "archive" / "restore" | `a-inf rebuild` | `wiki-rebuild` |
| "link my pages" / "cross-reference" | `a-inf fixlink` | `wiki-fixlink` |
| "fix my tags" / "normalize tags" | `a-inf tags` | `wiki-tags` |
| "update wiki" / "sync to wiki" | `a-inf update` | `wiki-update` |
| "export wiki" / "export graph" | `a-inf export` | `wiki-export` |
| "color my graph" | `a-inf colorize` | `wiki-colorize` |
| "save this" / "capture this" | `a-inf capture` | `wiki-capture` |
| "research X" | `a-inf research X` | `wiki-research` |
| "create a dashboard" | `a-inf dashboard` | `wiki-dashboard` |
| "synthesize my wiki" | `a-inf synthesize` | `wiki-synthesize` |
| "devise an idea" / "make an idea packet" | `a-inf ideate "<idea>"` | `wiki-ideate` |

## Cross-Project Usage

The main use case: you're working in a repository initialized with `a-inf init` and want to sync knowledge into the vault or query it. The CLI handles command routing; skills define the underlying behavior.

### wiki-update (write to wiki)

1. Read `.a-inf/config.toml`, falling back to `~/.obsidian-wiki/config` or `.env`, to get the vault path
2. Scan the current project: README, source structure, git log, package metadata
3. Distill what's worth remembering (architecture decisions, patterns, trade-offs — not code listings)
4. Write to `$VAULT/projects/<project-name>.md`, cross-linking to concept/entity pages as needed
5. Update `.manifest.json`, `index.md`, and `log.md`

On repeat runs, it checks `last_commit_synced` in `.manifest.json` and only processes the delta via `git log <last_commit>..HEAD`.

### wiki-query (read from wiki)

1. Read `.a-inf/config.toml`, falling back to `~/.obsidian-wiki/config` or `.env`, to get the vault path
2. Scan titles, tags, and `summary:` frontmatter fields first (cheap pass)
3. Only open page bodies when the index pass can't answer
4. Return a synthesized answer with `[[wikilink]]` citations

## Visibility Tags (optional)

Pages can carry a `visibility/` tag to mark their intended reach. **This is entirely optional** — untagged pages behave exactly as they always have (visible everywhere). The system stays single-vault, single source of truth.

| Tag | Meaning |
|---|---|
| *(no tag)* | Same as `visibility/public` — visible in all modes |
| `visibility/public` | Explicitly public — visible in all modes |
| `visibility/internal` | Team-only — excluded when querying in filtered mode |
| `visibility/pii` | Sensitive data — excluded when querying in filtered mode |

**Filtered mode** is opt-in, triggered by phrases like "public only", "user-facing answer", "no internal content", or "as a user would see it" in a query. Default mode shows everything.

`visibility/` tags are **system tags** — they don't count toward the 5-tag limit and are listed separately from domain/type tags in the taxonomy.

See `wiki-query` and `wiki-export` skills for how the filter is applied.

## Core Principles

- **Compile, don't retrieve.** The wiki is pre-compiled knowledge. Update existing pages — don't append or duplicate.
- **Track everything.** Update `.manifest.json` after ingesting, `index.md`, `log.md`, and `hot.md` after any write operation.
- **Connect with `[[wikilinks]]`.** Every page should link to related pages. This is what makes it a knowledge graph, not a folder of files.
- **Frontmatter is required.** Every wiki page needs: `title`, `category`, `tags`, `sources`, `created`, `updated`.
- **Single source of truth.** Visibility tags shape how content is surfaced — they don't duplicate or separate it.
- **Keep context warm.** `hot.md` is a ~500-word semantic snapshot of recent activity. Every write skill updates it so the next session can pick up where the last one left off without crawling the full vault.

## Architecture Reference

For the full pattern (three-layer architecture, page templates, project org), read `.skills/llm-wiki/SKILL.md`.
