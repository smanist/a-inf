# Setup

`a-inf` is now CLI-first. Install it from this checkout, then initialize any repository as a vault.

## Install

```bash
pip install -e .
npm install -g @tobilu/qmd
```

QMD uses a SQLite-backed local index, including sqlite-vec data. `a-inf` keeps its QMD state repo-local under `.a-inf/qmd/` and passes `INDEX_PATH`, `XDG_CACHE_HOME`, and `XDG_CONFIG_HOME` to Codex so sandboxed QMD queries open the same writable database.

Verify:

```bash
a-inf --help
qmd --version
```

## Initialize A Vault

From the repo that should become the vault:

```bash
a-inf init
```

Or pass a path:

```bash
a-inf init /path/to/repo
```

Initialization creates:

- `.a-inf/config.toml`
- `.env` compatibility config
- `.gitignore` entries for local config
- `.manifest.json`
- `index.md`, `log.md`, `hot.md`
- `_meta/taxonomy.md`
- `.obsidian/app.json` and `.obsidian/appearance.json`
- vault folders: `concepts/`, `entities/`, `skills/`, `references/`, `synthesis/`, `journal/`, `projects/`, `_archives/`, `_raw/`
- `.skills/<skill-name>` symlinks to this checkout's bundled skills
- an `a-inf` section in `AGENTS.md`, unless `--no-agents` is passed

The generated `.env` defaults `QMD_WIKI_COLLECTION` and `QMD_PAPERS_COLLECTION` to the initialized repo directory name. Init also creates that QMD collection and runs the first `qmd update` / `qmd embed`.

Use `--copy-skills` if symlinks are not appropriate:

```bash
a-inf init --copy-skills
```

Use `--no-gitignore` if you do not want initialization to touch `.gitignore`:

```bash
a-inf init --no-gitignore
```

Use `--write-global-config` only if you still want the legacy global config:

```bash
a-inf init --write-global-config
```

## Run Workflows

```bash
a-inf ingest paper-xx
a-inf ingest https://example.com/article
a-inf query "what do I know about X?"
a-inf status
a-inf insights
a-inf update
a-inf history
```

Most non-init workflows invoke `codex exec --sandbox workspace-write --cd <vault>` with a prompt generated from the relevant skill. `a-inf ingest` is the exception: it runs deterministic source selection and plan validation around a Codex-generated JSON ingest plan. To inspect the ingest packet without invoking Codex:

```bash
a-inf ingest paper-xx --print-prompt
```

If the source is outside the initialized vault, grant Codex access to that directory:

```bash
a-inf ingest /path/to/source.md --add-dir /path/to
```

`a-inf status` runs locally. `a-inf history` automatically grants Codex access to `CODEX_HISTORY_PATH` or `~/.codex` when that directory exists.

After successful write workflows, `a-inf` refreshes QMD with:

```bash
qmd update
qmd embed
```

If QMD refresh fails after a workflow has already written vault files, the CLI prints a warning instead of rolling back the vault.

Ingest modes:

```bash
a-inf ingest --mode append  # default, only new or modified sources
a-inf ingest --mode full    # all supported configured sources
a-inf ingest --raw          # promote files from _raw/
```

## Command Mapping

| CLI | Skill |
|---|---|
| `a-inf ingest <source>` | `wiki-ingest` |
| `a-inf ingest <url>` | `wiki-ingest` |
| `a-inf ingest --data <source>` | `data-ingest` |
| `a-inf query` | `wiki-query` |
| `a-inf status` | deterministic CLI |
| `a-inf insights` | `wiki-insights` |
| `a-inf update` | `wiki-update` |
| `a-inf history` | `codex-history-ingest` |
| `a-inf lint` | `wiki-lint` |
| `a-inf rebuild` | `wiki-rebuild` |
| `a-inf export` | `wiki-export` |
| `a-inf research` | `wiki-research` |
| `a-inf capture` | `wiki-capture` |
| `a-inf synthesize` | `wiki-synthesize` |
| `a-inf dashboard` | `wiki-dashboard` |
| `a-inf colorize` | `graph-colorize` |
| `a-inf cross-link` | `cross-linker` |
| `a-inf tags` | `tag-taxonomy` |

## Config

CLI-native config lives at `.a-inf/config.toml`:

```toml
vault_path = "/path/to/repo"
skills_source = "/path/to/a-inf/.skills"
link_format = "wikilink"
```

`a-inf init` also writes `.env` when absent so existing skills keep working while the migration is in progress. QMD collection values default to the initialized repo name and are initialized automatically. Legacy global config is still available through `~/.obsidian-wiki/config`.

## Open In Obsidian

Open the initialized repo directory in Obsidian with File -> Open Vault.
