# Setup

This repo is a Codex-only, skill-only framework for maintaining an Obsidian wiki. Codex reads the markdown skills in `.skills/` and writes standard markdown pages into your vault.

There is no setup script. Configuration is a small env file plus optional symlinks into Codex's global skills directory.

## 1. Configure Your Vault

Copy the example config:

```bash
cp .env.example .env
```

Set the required vault path:

```env
OBSIDIAN_VAULT_PATH=/path/to/your/vault
```

That directory can be a new folder or an existing Obsidian vault.

For use from any project, create `~/.obsidian-wiki/config`:

```env
OBSIDIAN_VAULT_PATH=/path/to/your/vault
OBSIDIAN_WIKI_REPO=/path/to/obsidian-wiki
```

Skills read the global config first, then fall back to `.env` in this repo.

## 2. Make Skills Available To Codex

When working inside this repo, Codex can read `.skills/` directly through `AGENTS.md`.

For global use, symlink the skill directories:

```bash
mkdir -p ~/.codex/skills
ln -s /path/to/obsidian-wiki/.skills/* ~/.codex/skills/
```

If a symlink already exists, leave it in place or replace it manually.

## 3. Use The Workflows

| What you say | Skill |
|---|---|
| "Set up my wiki" | `wiki-setup` |
| "Ingest my documents from ~/research" | `wiki-ingest` |
| "Add this URL to my wiki" | `ingest-url` |
| "Import my Codex history" | `codex-history-ingest` |
| "$wiki-history-ingest codex" | `wiki-history-ingest` |
| "Process this ChatGPT export" | `data-ingest` |
| "What's the status of my wiki?" | `wiki-status` |
| "What do I know about X?" | `wiki-query` |
| "Audit my wiki" | `wiki-lint` |
| "Rebuild from scratch" | `wiki-rebuild` |
| "Sync this project to my wiki" | `wiki-update` |

Codex reads the relevant `SKILL.md`, resolves the vault path, checks `.manifest.json`, and performs the requested operation.

## What Can It Ingest?

| Source | Skill | What it reads |
|---|---|---|
| Markdown, PDFs, text files | `wiki-ingest` | Any document directory or explicit file list |
| Web pages | `ingest-url` | A user-provided URL |
| Codex history | `codex-history-ingest` | `~/.codex/` sessions, rollouts, and history index |
| ChatGPT exports | `data-ingest` | `conversations.json` or equivalent export files |
| Slack / Discord logs | `data-ingest` | Channel export JSON or text logs |
| Meeting transcripts | `data-ingest` | Any text transcript |
| Raw text dumps | `data-ingest` | CSV, logs, journals, notes, and pasted exports |

## Tracking And Delta

The framework tracks ingested sources in `$OBSIDIAN_VAULT_PATH/.manifest.json`.

This enables:

- Status checks for ingested, pending, and changed sources.
- Append-mode ingestion that only processes new or modified files.
- Provenance from source files to wiki pages.
- Staleness detection when a source changed after a page was written.

Typical flow:

```text
"What's the status?"     -> wiki-status computes the delta
"Ingest the new stuff"   -> wiki-ingest processes only changed sources
"What's the status now?" -> wiki-status confirms the vault is current
```

## Vault Structure

```text
$OBSIDIAN_VAULT_PATH/
├── index.md
├── log.md
├── hot.md
├── .manifest.json
├── _meta/
│   └── taxonomy.md
├── _raw/
├── concepts/
├── entities/
├── skills/
├── references/
├── synthesis/
├── journal/
└── projects/
    └── <project-name>.md
```

Knowledge that's project-specific goes under `projects/`. Knowledge that's reusable goes in the global category folders. Both are cross-referenced with `[[wikilinks]]`.

## Optional Config

| Variable | What it does | Default |
|---|---|---|
| `OBSIDIAN_SOURCES_DIR` | Directories with docs to ingest, comma-separated | empty |
| `OBSIDIAN_CATEGORIES` | Wiki page categories | `concepts,entities,skills,references,synthesis,journal` |
| `OBSIDIAN_MAX_PAGES_PER_INGEST` | Max pages updated per ingest operation | `15` |
| `CODEX_HISTORY_PATH` | Where to find Codex data | `~/.codex` |
| `LINT_SCHEDULE` | Wiki health check frequency | `weekly` |
| `OBSIDIAN_LINK_FORMAT` | `wikilink` or `markdown` links for future writes | `wikilink` |
| `OBSIDIAN_RAW_DIR` | Vault-relative staging directory for rough captures | `_raw` |
| `QMD_WIKI_COLLECTION` | Optional QMD collection for compiled wiki pages | empty |
| `QMD_PAPERS_COLLECTION` | Optional QMD collection for source documents | empty |

## Skills Reference

| Skill | Purpose |
|---|---|
| `llm-wiki` | Core pattern, architecture, page templates, and project organization |
| `wiki-setup` | Initialize vault structure and baseline files |
| `wiki-ingest` | Distill documents and staged notes into wiki pages |
| `ingest-url` | Distill a web page into the wiki |
| `wiki-history-ingest` | Codex history alias/router |
| `codex-history-ingest` | Mine `~/.codex` sessions and rollout logs |
| `data-ingest` | Ingest raw text, exports, logs, and transcripts |
| `wiki-status` | Show ingest state, pending deltas, and graph insights |
| `wiki-query` | Answer questions from the compiled wiki with citations |
| `wiki-update` | Sync current project knowledge into the vault |
| `wiki-lint` | Find orphans, broken links, stale content, and contradictions |
| `wiki-rebuild` | Archive, rebuild from scratch, or restore |
| `cross-linker` | Insert missing wikilinks between existing pages |
| `tag-taxonomy` | Normalize tag vocabulary |
| `graph-colorize` | Configure Obsidian graph color groups |
| `wiki-export` | Export the wiki graph |
| `wiki-dashboard` | Create Obsidian Bases dashboard views |
| `wiki-capture` | Save the current conversation as a wiki note |
| `wiki-research` | Research and file a topic |
| `wiki-synthesize` | Discover and fill synthesis gaps |

## Open In Obsidian

Open `OBSIDIAN_VAULT_PATH` in Obsidian with File -> Open Vault. The generated markdown, frontmatter, wikilinks, graph view, and Bases files are native Obsidian artifacts.
