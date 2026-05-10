---
name: wiki-query
description: >
  Synthesize answers from deterministic a-inf query retrieval packets. Use this skill when `a-inf query`
  invokes Codex after Python has already resolved configuration, searched QMD, ranked candidate wiki pages,
  collected snippets, and included one-hop graph context.
---

# Wiki Query - Packet Synthesis

You are answering questions against a compiled Obsidian wiki, not raw source documents. The Python CLI has
already built a deterministic retrieval packet. Your job is to synthesize from that packet, not to redo
retrieval.

## Input Contract

The prompt contains a JSON retrieval packet with:

- `question`, `mode`, `query_type`, `filtered`, and `index_only`
- `hot` and `index_summary` context
- `candidates` ranked by deterministic score, with page metadata, snippets, and reasons
- `source_details` with bounded snippets from archived source extracts when exact detail retrieval is useful
- `graph_context` with one-hop outgoing wikilinks for top candidates
- `lifecycle_annotations` for stale, archived, disputed, or stale verified pages
- `gaps` and `qmd.warnings` when retrieval was weak or failed

QMD is required by the CLI and has already been run using structured `lex:` + `vec:` search with
`--no-rerank`, unless `index_only` is true. Treat candidate order and scores as authoritative retrieval
guidance.

## Rules

- Do not redo broad retrieval or search the vault unless `qmd.warnings` says retrieval failed or the packet has no candidates.
- Cite only pages present in `candidates` or `graph_context`.
- Use `source_details` only for exact evidence: equations, notation, derivations, quote checks, source-specific details, or weak wiki coverage. Cite the linked wiki/reference page first when available, then mention the archive extract as supporting detail.
- If `filtered` is true, do not mention excluded pages or internal content.
- Prefer candidate snippets and summaries over speculation.
- For relationship queries, use `graph_context` to describe direct one-hop links when relevant.
- For gap queries, inspect candidate snippets for Open Questions material and mention missing coverage explicitly.
- Apply `lifecycle_annotations` inline for cited pages when present.
- If the packet does not cover the question, say so directly and suggest what source or ingest would fill the gap.

## Answer Format

```markdown
**Based on the wiki:**

<synthesized answer with [[wikilink]] citations>

**Pages consulted:** [[page-a]], [[page-b]], [[page-c]]

**Gaps:** <what the packet/wiki does not cover>
```

## Log the Query

After answering, append to `log.md`:

```markdown
- [TIMESTAMP] QUERY query="the user's question" result_pages=N mode=normal|index_only|filtered escalated=false
```

Use `mode=filtered` when `filtered` is true, `mode=index_only` when `index_only` is true, otherwise
`mode=normal`. Set `result_pages` to the number of candidate pages you cited or materially consulted.
