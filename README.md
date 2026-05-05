# obsidian-wiki

A Codex skill pack for building and maintaining an Obsidian knowledge base using Andrej Karpathy's "LLM Wiki" pattern: compile durable knowledge once into interconnected markdown pages, then keep it current as you work.

The repo is skill-only. There are no setup scripts, runtime dependencies, or services. Codex reads markdown instructions from `.skills/`, writes standard Obsidian-compatible markdown into your vault, and maintains the wiki graph over time.

## What It Does

- Distills documents, notes, transcripts, exports, and Codex sessions into wiki pages.
- Updates existing pages instead of appending duplicate notes.
- Maintains `index.md`, `log.md`, `hot.md`, and `.manifest.json`.
- Uses `[[wikilinks]]` by default so the vault works natively in Obsidian.
- Supports project pages, graph exports, linting, dashboards, cross-linking, and synthesis.

## Quick Start

1. Copy the example config:

   ```bash
   cp .env.example .env
   ```

2. Set `OBSIDIAN_VAULT_PATH` in `.env` to the absolute path of your Obsidian vault:

   ```env
   OBSIDIAN_VAULT_PATH=/path/to/your/vault
   ```

3. Open this repo with Codex and ask for the workflow you want:

   ```text
   set up my wiki
   ingest these docs
   import my Codex history
   what do I know about rate limiting?
   ```

For global use from other projects, keep a global config at `~/.obsidian-wiki/config`:

```env
OBSIDIAN_VAULT_PATH=/path/to/your/vault
OBSIDIAN_WIKI_REPO=/path/to/obsidian-wiki
```

Then copy or symlink the skills into Codex's skill directory:

```bash
mkdir -p ~/.codex/skills
ln -s /path/to/obsidian-wiki/.skills/* ~/.codex/skills/
```

## Codex Compatibility

Codex uses:

- `AGENTS.md` for always-on project context and routing.
- `.skills/<name>/SKILL.md` for workflow instructions.
- `~/.codex/skills/` when you want the skills available outside this repo.
- `$skill-name` style invocation when explicitly naming a skill, although natural language works too.

This repo intentionally includes only Codex-relevant history ingest. The only local agent history source is `~/.codex/`.

## Core Workflow

Every ingest follows the same pattern:

1. **Ingest**: Read source material directly from files, URLs, exports, raw notes, or Codex history.
2. **Extract**: Pull out concepts, entities, claims, decisions, procedures, and open questions.
3. **Resolve**: Merge into existing wiki pages when possible; create new pages only for genuinely new knowledge.
4. **Connect**: Add `[[wikilinks]]`, update the manifest, refresh the index, log the operation, and update `hot.md`.

The result is a compiled knowledge base, not a transcript archive.

## Vault Structure

```text
$OBSIDIAN_VAULT_PATH/
├── index.md
├── log.md
├── hot.md
├── .manifest.json
├── _meta/
│   ├── taxonomy.md
│   └── *.base
├── _insights.md
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

Every wiki page has frontmatter with `title`, `category`, `tags`, `sources`, `created`, and `updated`. New or updated pages should also include a short `summary:` field when the relevant skill calls for it.

## Skills

| Skill | Purpose |
|---|---|
| `wiki-setup` | Initialize vault structure and baseline files |
| `wiki-ingest` | Distill documents and staged notes into wiki pages |
| `ingest-url` | Save and distill web pages into the wiki |
| `wiki-history-ingest` | Codex history alias/router |
| `codex-history-ingest` | Mine `~/.codex` sessions and rollout logs |
| `data-ingest` | Process exports, logs, transcripts, and raw text |
| `wiki-status` | Show ingest state, deltas, and graph insights |
| `wiki-query` | Answer questions from the compiled wiki with citations |
| `wiki-update` | Sync current project knowledge into the vault |
| `wiki-lint` | Find broken links, missing metadata, stale pages, and orphaned notes |
| `wiki-rebuild` | Archive, rebuild, or restore the wiki |
| `cross-linker` | Add missing wikilinks between existing pages |
| `tag-taxonomy` | Maintain controlled tag vocabulary |
| `graph-colorize` | Configure Obsidian graph colors |
| `wiki-export` | Export the wiki graph to JSON, GraphML, Cypher, and HTML |
| `wiki-dashboard` | Create Obsidian Bases dashboard views |
| `wiki-capture` | Save the current conversation as a wiki note |
| `wiki-research` | Research a topic and file the results |
| `wiki-synthesize` | Find and fill synthesis gaps across the wiki |
| `llm-wiki` | Architecture reference for the wiki pattern |

## Codex History Ingest

`codex-history-ingest` reads Codex artifacts under `CODEX_HISTORY_PATH`, defaulting to `~/.codex`:

```text
~/.codex/
├── sessions/
│   └── YYYY/MM/DD/rollout-*.jsonl
├── archived_sessions/
├── session_index.jsonl
├── history.jsonl
└── config.toml
```

It uses append mode by default, comparing each source file against `.manifest.json` and processing only new or modified history. It distills durable knowledge from sessions while filtering tool plumbing, telemetry, injected prompts, and sensitive data.

## Optional QMD Search

The skills work with regular filesystem search by default. If you use [QMD](https://github.com/tobi/qmd), set these optional collections in `.env`:

```env
QMD_WIKI_COLLECTION=wiki
QMD_PAPERS_COLLECTION=papers
```

`wiki-query` can use the wiki collection for semantic lookup, and `wiki-ingest` can use the papers collection to find related source material before writing.

## Project Layout

```text
obsidian-wiki/
├── AGENTS.md
├── README.md
├── SETUP.md
├── .env.example
└── .skills/
    ├── codex-history-ingest/
    ├── cross-linker/
    ├── data-ingest/
    ├── graph-colorize/
    ├── ingest-url/
    ├── llm-wiki/
    ├── tag-taxonomy/
    ├── wiki-capture/
    ├── wiki-dashboard/
    ├── wiki-export/
    ├── wiki-history-ingest/
    ├── wiki-ingest/
    ├── wiki-lint/
    ├── wiki-query/
    ├── wiki-rebuild/
    ├── wiki-research/
    ├── wiki-setup/
    ├── wiki-status/
    ├── wiki-synthesize/
    └── wiki-update/
```

## Extending

Add a new workflow by creating `.skills/<skill-name>/SKILL.md` with YAML frontmatter:

```yaml
---
name: your-skill-name
description: >
  What this skill does and when Codex should use it.
---
```

Keep new workflows as markdown skill instructions so the repo stays skill-only.
