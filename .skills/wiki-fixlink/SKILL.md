---
name: wiki-fixlink
description: >
  Semantic repair planner for `a-inf fixlink`. The CLI builds a deterministic
  link-health packet from wiki-lint evidence, then this skill reviews bounded
  candidates and writes a JSON repair plan. Python validates and applies edits.
---

# Wiki Fixlink — Packet-First Link Repair

`a-inf fixlink` is packet-first:

1. Python builds a deterministic packet from the lint page registry, graph, hard findings, and fixlink candidates.
2. Codex reads that packet and this skill.
3. Codex writes only `repair_plan.json`.
4. Python validates every decision and applies link edits, link formatting, misc affinity updates, `log.md`, and `hot.md`.

Do **not** edit wiki files, `log.md`, `index.md`, `.manifest.json`, `hot.md`, or the deterministic packet.

## Inputs

The CLI prompt includes:

- `Vault path`
- `Deterministic packet path`
- `Write repair plan JSON to`

Read the deterministic packet first. Important fields:

- `candidates` — bounded link repair candidates with stable `candidate_id` values.
- `candidates[].kind` — `inline` or `related`.
- `candidates[].matches` — deterministic inline match choices; use only these `match_id` values.
- `lint_packet.page_registry` — authoritative page metadata.
- `lint_packet.graph` — authoritative resolved/broken edge evidence.
- `lint_packet.findings` — hard evidence such as orphans, fragmented tag clusters, and misc promotion candidates.

Treat the packet as authoritative. Do not recompute the graph or rescan the whole vault.

## Decision Rules

For each candidate you inspect:

- Use `add_inline` when the packet shows a natural exact mention and the link target is semantically correct.
- Use `add_related` when the pages are meaningfully connected but no inline mention is the right edit.
- Use `skip` when the relationship is weak, incidental, redundant, ambiguous, or likely to clutter the page.

Prefer fewer high-confidence links. Do not approve a candidate just because pages share a tag.

## Inline Links

For `add_inline`:

- The candidate must have `kind: "inline"`.
- Choose exactly one `match_id` from the candidate's `matches`.
- Prefer the first natural prose mention over headings, list boilerplate, or repetitive mentions.

Python will compute the final wikilink or Markdown link syntax.

## Related Links

For `add_related`:

- Include a short `note` explaining the relationship.
- Keep the note factual and concise.
- Do not include link syntax in the note.

Python will append or create the `## Related` section and compute link syntax.

## Output Contract

Write exactly one JSON object to the path named by `Write repair plan JSON to`. Do not print Markdown.

Required shape:

```json
{
  "version": 1,
  "status": "completed",
  "decisions": [],
  "warnings": []
}
```

Decision shapes:

```json
{
  "candidate_id": "fixlink-0001",
  "action": "add_inline",
  "match_id": "m001"
}
```

```json
{
  "candidate_id": "fixlink-0002",
  "action": "add_related",
  "note": "Shares the same low-cohesion systems cluster."
}
```

```json
{
  "candidate_id": "fixlink-0003",
  "action": "skip"
}
```

`action` must be one of `add_inline`, `add_related`, or `skip`.

Put non-fatal issues in `warnings`, such as candidates that need more context than the packet provides.

## Hard Constraints

- Do not mutate the deterministic packet.
- Do not write anything except `repair_plan.json`.
- Do not append to `log.md`; the CLI does that.
- Do not fabricate page evidence. If the packet is insufficient, choose `skip` or add a warning.
- Do not invent candidate IDs or match IDs.
