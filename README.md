# a-inf

`a-inf` is a CLI for turning a repository into an Obsidian-backed knowledge vault and operating it through repeatable commands.

The markdown skills in `.skills/` are still the workflow specs, but users should not need to call those skills directly. The CLI handles local initialization and dispatches higher-level operations such as `a-inf ingest paper-xx`. For complex language-model work, the CLI can invoke Codex with the selected skill.

## Install For Development

```bash
pip install -e .
```

This exposes the `a-inf` command from the current checkout.

## Quick Start

From the repository you want to use as a vault:

```bash
a-inf init
a-inf ingest paper-xx
a-inf status
a-inf query "what do I know about rate limiting?"
```

`a-inf init` is local and deterministic. It creates the vault folders, seed files, `.a-inf/config.toml`, a compatibility `.env`, Obsidian config, `.gitignore` entries for local config, and local skill symlinks under `.skills/`.

The other commands generate a Codex prompt from the matching skill and invoke `codex exec` when Codex is available. Use `--print-prompt` to inspect the generated prompt instead:

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
├── .skills/                 # symlinks to bundled workflow skills
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
| `a-inf ingest <source...>` | Ingest documents or staged content |
| `a-inf ingest <url>` | Route URL ingest to `ingest-url` |
| `a-inf query <question>` | Answer from the compiled vault |
| `a-inf status` | Show ingest state and deltas |
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
| `a-inf cross-link` | Add missing wikilinks |
| `a-inf tags` | Normalize tag taxonomy |
| `a-inf skill <name> ...` | Dispatch any bundled skill by name |

## Codex Dispatch

Command dispatch is intentionally thin during this migration. For example:

```bash
a-inf ingest paper-xx
```

maps to `wiki-ingest`, constructs a prompt with the vault path, selected skill file, and CLI arguments, then runs:

```bash
codex exec "<generated prompt>"
```

Commands that can be fully deterministic should move into Python over time. Commands that require synthesis can keep using Codex behind the CLI.

## Configuration

`a-inf init` writes `.a-inf/config.toml`:

```toml
vault_path = "/absolute/path/to/repo"
skills_source = "/absolute/path/to/a-inf/.skills"
link_format = "wikilink"
```

It also writes a minimal `.env` when one does not already exist, because some skills still read legacy env config during the migration. `.a-inf/config.toml` is the CLI-native config.

Optional environment variables from `.env.example` still apply for skill workflows, including:

- `OBSIDIAN_SOURCES_DIR`
- `CODEX_HISTORY_PATH`
- `OBSIDIAN_LINK_FORMAT`
- `OBSIDIAN_RAW_DIR`
- `QMD_WIKI_COLLECTION`
- `QMD_PAPERS_COLLECTION`

## Skills

The bundled skills remain the source of truth for language-model workflows:

```text
.skills/
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
├── wiki-status/
├── wiki-synthesize/
└── wiki-update/
```

## Direction

The target architecture is CLI-first:

- `init`, config, folder creation, and symlink management live in Python.
- There is no setup skill; initialization is deterministic CLI execution.
- Read-only status and deterministic maintenance should move into Python next.
- High-level synthesis commands can continue to invoke Codex with the appropriate skill.
- Skills become internal execution specs rather than the user-facing interface.
