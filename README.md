# a-inf

`a-inf` is a CLI for turning a repository into an Obsidian-backed knowledge vault and operating it through repeatable commands.

The markdown skills in `.skills/` are still the workflow specs, but users should not need to call those skills directly. The CLI handles local initialization and dispatches higher-level operations such as `a-inf ingest paper-xx`. For complex language-model work, the CLI can invoke Codex with the selected skill.

## Install For Development

```bash
pip install -e .
npm install -g @tobilu/qmd
npm install -g defuddle
```

This exposes the `a-inf` command from the current checkout. `qmd` is a required runtime CLI dependency for initialization, ingest, and query; `defuddle` is required for URL ingest via `a-inf ingest <url>`. `mineru` is optional for PDF extraction. `a-inf` keeps QMD's writable SQLite/sqlite-vec index and collection config under `.a-inf/qmd/`.

## Quick Start

From the repository you want to use as a vault:

```bash
a-inf init
a-inf ingest paper-xx
a-inf status
a-inf insights
a-inf query "what do I know about rate limiting?"
```

`a-inf init` is local and deterministic. It creates the vault folders, seed files, `.a-inf/config.toml`, a compatibility `.env`, Obsidian config, `.gitignore` entries for local config, and local Codex skill symlinks under `.agents/skills/`. New `.env` files default `QMD_WIKI_COLLECTION` and `QMD_PAPERS_COLLECTION` to the repo directory name, and init creates the matching QMD collection.

`a-inf ingest` now runs a hybrid deterministic engine: Python selects sources, extracts URL content with `defuddle`, optionally extracts PDF markdown with MinerU, computes hashes, asks Codex for a JSON ingest plan, validates the whole plan, and only then writes wiki files. `a-inf query` also starts deterministically: Python builds a QMD-backed retrieval packet, then asks Codex only to synthesize the final cited answer. `a-inf lint` follows the same hybrid pattern: Python builds a deterministic health packet, then Codex can append semantic review findings for contradictions and synthesis gaps. `a-inf insights` computes graph facts deterministically and uses Codex only for optional explanation text. `a-inf fixlink` uses lint's deterministic graph packet to ask Codex for bounded link decisions, then Python validates and applies the edits. After successful write workflows, the CLI refreshes QMD with `qmd update` and `qmd embed`. Other synthesis-heavy commands still dispatch to Codex with the matching skill. Use `--print-prompt` to inspect the generated ingest packet, query packet, lint semantic-review prompt, insights explanation prompt, fixlink packet, or dispatch prompt instead:

```bash
a-inf ingest paper-xx --print-prompt
```

## What `a-inf init` Creates

```text
repo/
├── .a-inf/
│   └── config.toml
├── .env
├── .gitignore
├── .manifest.json
├── AGENTS.md
├── index.md
├── log.md
├── hot.md
├── _meta/
│   └── taxonomy.md
├── _raw/
├── .obsidian/
├── .agents/
│   └── skills/              # symlinks to bundled workflow skills
├── concepts/
├── entities/
├── skills/
├── references/
├── synthesis/
├── journal/
└── projects/
```

If `AGENTS.md` already exists, `a-inf init` appends a small marked `a-inf` section instead of replacing the file. Pass `--no-agents` to skip that. Pass `--no-gitignore` to leave `.gitignore` untouched.

## Commands

| Command | Workflow |
|---|---|
| `a-inf init [path]` | Initialize a repo as a vault |
| `a-inf ingest <source...>` | Hybrid deterministic document and URL ingest |
| `a-inf ingest <url>` | Fetch with `defuddle`, then run hybrid ingest |
| `a-inf query <question>` | Answer from the compiled vault |
| `a-inf status` | Show ingest state and deltas locally |
| `a-inf insights` | Analyze hubs, bridges, and graph structure |
| `a-inf update` | Sync current project knowledge into the vault |
| `a-inf history` | Mine local Codex history from `~/.codex` |
| `a-inf lint` | Audit links, metadata, stale pages, and orphans |
| `a-inf rebuild` | Archive, rebuild, or restore |
| `a-inf export` | Export the graph |
| `a-inf research <topic>` | Research and file a topic |
| `a-inf capture` | Capture the current conversation |
| `a-inf synthesize` | Find synthesis gaps |
| `a-inf dashboard` | Create Obsidian Bases dashboards |
| `a-inf colorize` | Configure graph colors |
| `a-inf fixlink` | Add missing wikilinks |
| `a-inf tags` | Audit and normalize wiki tags |
| `a-inf skill <name> ...` | Dispatch any bundled skill by name |

## Codex Dispatch

Most command dispatch is intentionally thin during this migration, but document ingest has moved to a two-phase engine. For example:

```bash
a-inf ingest paper-xx
```

selects new or modified sources, writes a run packet under `.a-inf/runs/`, asks Codex to create `.a-inf/runs/<run-id>/plan.json`, validates the JSON, then applies page, manifest, index, log, and hot-cache updates deterministically. URL sources are fetched with `defuddle` before planning and land under `references/`; PDF sources can include cached MinerU markdown under `.a-inf/mineru/`; exports, logs, and transcripts use the same hybrid ingest path.

The default mode is append. Full and raw modes are available:

```bash
a-inf ingest --mode full
a-inf ingest --raw
```

Use `--sandbox read-only` for dry inspection workflows that still invoke Codex, or `--add-dir <path>` when an ingest source lives outside the vault and Codex needs access to it. `a-inf history` automatically adds `CODEX_HISTORY_PATH` or `~/.codex` when that directory exists.

Commands that can be fully deterministic should move into Python over time. `a-inf status` is deterministic local Python, and `a-inf insights` is deterministic graph analysis with optional Codex explanation enrichment. Commands that require synthesis can keep using Codex behind the CLI.

`a-inf lint --json` prints the full health packet, including deterministic findings and semantic candidates. Plain `a-inf lint` renders human Markdown and hides raw semantic candidates unless Codex promotes them. Use `a-inf lint --no-codex` for deterministic-only output, `--semantic-scope broad` to allow wider semantic review, and `--no-log` to avoid appending the default `LINT` entry to `log.md`.

## Configuration

`a-inf init` writes `.a-inf/config.toml`:

```toml
vault_path = "/absolute/path/to/repo"
skills_source = "/absolute/path/to/a-inf/.skills"
link_format = "wikilink"
```

It also writes a minimal `.env` when one does not already exist, because some skills still read legacy env config during the migration. `.a-inf/config.toml` is the CLI-native config. The generated QMD collection values default to the initialized repo name; init runs `qmd collection add <repo> --name <repo-name>`, then `qmd update` and `qmd embed`.

Optional environment variables from `.env.example` still apply for skill workflows, including:

- `OBSIDIAN_SOURCES_DIR`
- `CODEX_HISTORY_PATH`
- `OBSIDIAN_LINK_FORMAT`
- `OBSIDIAN_RAW_DIR`
- `QMD_WIKI_COLLECTION`
- `QMD_PAPERS_COLLECTION`
- `A_INF_PDF_EXTRACTOR` (`auto`, `mineru`, or `none`)
- `A_INF_MINERU_BIN`
- `A_INF_MINERU_METHOD`
- `A_INF_MINERU_BACKEND`
- `A_INF_MINERU_LANG`
- `A_INF_MINERU_FORMULA`
- `A_INF_MINERU_TABLE`

## Skills

The bundled skills remain the source of truth for language-model workflows:

```text
.skills/
├── codex-history-ingest/
├── graph-colorize/
├── llm-wiki/
├── wiki-tags/
├── wiki-capture/
├── wiki-dashboard/
├── wiki-export/
├── wiki-fixlink/
├── wiki-history-ingest/
├── wiki-insights/
├── wiki-ingest/
├── wiki-lint/
├── wiki-query/
├── wiki-rebuild/
├── wiki-research/
├── wiki-synthesize/
└── wiki-update/
```

## Direction

The target architecture is CLI-first:

- `init`, config, folder creation, and symlink management live in Python.
- There is no setup skill; initialization is deterministic CLI execution.
- Read-only status lives in Python.
- High-level synthesis commands can continue to invoke Codex with the appropriate skill.
- Skills become internal execution specs rather than the user-facing interface.
