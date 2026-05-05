---
name: wiki-setup
description: >
  Legacy skill spec for initializing an Obsidian wiki vault. Prefer `a-inf init` for normal use.
  Use this skill only when Codex is explicitly repairing setup or explaining the initialization workflow.
---

# Obsidian Setup — Vault Initialization

Prefer the CLI path:

```bash
a-inf init
```

The CLI creates folders, seed files, `.a-inf/config.toml`, Obsidian config, and local skill symlinks. Use the rest of this skill as the legacy/manual setup contract or as repair guidance.

## Step 1: Create .env

If `.env` doesn't exist, create it from `.env.example`. Ask the user for:

1. **Where should the vault live?** → `OBSIDIAN_VAULT_PATH`
   - Default: `~/Documents/obsidian-wiki-vault`
   - Must be an absolute path (after expansion)

2. **Where are your source documents?** → `OBSIDIAN_SOURCES_DIR`
   - Can be multiple paths, comma-separated
   - Default: `~/Documents`

3. **Want to import Codex history?** -> `CODEX_HISTORY_PATH`
   - Default: `~/.codex`
   - Set explicitly if Codex data is elsewhere

4. **Have QMD installed?** → `QMD_WIKI_COLLECTION` / `QMD_PAPERS_COLLECTION`
   - Optional. Enables semantic search in `wiki-query` and source discovery in `wiki-ingest`.
   - If unsure, skip for now — both skills fall back to `Grep` automatically.
   - Install instructions: see `.env.example` (QMD section).

## Step 2: Create Vault Directory Structure

```bash
mkdir -p "$OBSIDIAN_VAULT_PATH"/{concepts,entities,skills,references,synthesis,journal,projects,_archives,_raw,.obsidian}
```

- `.obsidian/` — Obsidian's own config. Creates vault recognition.
- `projects/` — Per-project knowledge (populated during ingest).
- `_archives/` — Stores wiki snapshots for rebuild/restore operations.
- `_raw/` — Staging area for unprocessed drafts. Drop rough notes here; `wiki-ingest` will promote them to proper wiki pages and delete the originals.

## Step 3: Create Special Files

### index.md

```markdown
---
title: Wiki Index
---

# Wiki Index

*This index is automatically maintained. Last updated: TIMESTAMP*

## Concepts

*No pages yet. Use `a-inf ingest <source>` to add your first source.*

## Entities

## Skills

## References

## Synthesis

## Journal
```

### log.md

```markdown
---
title: Wiki Log
---

# Wiki Log

- [TIMESTAMP] INIT vault_path="OBSIDIAN_VAULT_PATH" categories=concepts,entities,skills,references,synthesis,journal
```

### hot.md

```markdown
---
title: Hot Cache
updated: TIMESTAMP
---

# Hot Cache

*A ~500-word semantic snapshot of recent activity. Updated after every major write operation.*

## Recent Activity

- [TIMESTAMP] INIT — vault created at OBSIDIAN_VAULT_PATH

## Active Threads

*None yet — start ingesting sources to populate.*

## Key Takeaways

*None yet.*

## Flagged Contradictions

*None yet.*
```

## Step 4: Create .obsidian Configuration

Create minimal Obsidian config for a good out-of-box experience:

### .obsidian/app.json
```json
{
  "strictLineBreaks": false,
  "showFrontmatter": false,
  "defaultViewMode": "preview",
  "livePreview": true
}
```

### .obsidian/appearance.json
```json
{
  "baseFontSize": 16
}
```

## Step 5: Recommend Obsidian Plugins

Tell the user about these recommended community plugins (they install manually):

1. **Dataview** — Query page metadata, create dynamic tables. Essential for a wiki.
2. **Graph Analysis** — Enhanced graph view for exploring connections.
3. **Templater** — If they want to create pages manually using templates.
4. **Obsidian Git** — Auto-backup the vault to a git repo.

## Step 6: Verify Setup

Run a quick sanity check:
- [ ] Vault directory exists with: `concepts/`, `entities/`, `skills/`, `references/`, `synthesis/`, `journal/`, `projects/`, `_archives/`, `_raw/`
- [ ] `index.md` exists at vault root
- [ ] `log.md` exists at vault root
- [ ] `hot.md` exists at vault root
- [ ] `.env` has `OBSIDIAN_VAULT_PATH` set
- [ ] `.obsidian/` directory exists
- [ ] Source directories (if configured) exist and are readable

Report the results and tell the user they can now:
1. Open the vault in Obsidian (File → Open Vault → select the directory)
2. Run `a-inf status` to see what's available to ingest
3. Run `a-inf ingest <source>` to add their first sources
4. Run `a-inf history` to mine their Codex sessions
5. Run `a-inf status` again anytime to check the delta
