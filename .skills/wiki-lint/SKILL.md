---
name: wiki-lint
description: >
  Semantic review layer for `a-inf lint`. The CLI now performs deterministic wiki health checks and
  passes this skill a JSON health packet. Use this skill to promote contradiction and synthesis-gap
  candidates into actual semantic findings, not to re-run mechanical lint checks.
---

# Wiki Lint — Semantic Review

`a-inf lint` is packet-first:

1. Python builds a deterministic health packet with hard findings and semantic candidates.
2. Codex reads that packet and this skill.
3. Codex writes a separate `semantic_review.json`.
4. The CLI validates and appends that object as `semantic_review` without letting it overwrite deterministic fields.

Do **not** edit wiki files, `log.md`, `index.md`, `.manifest.json`, `hot.md`, or the deterministic packet. The CLI owns packet generation, rendering, validation, and logging.

## Inputs

The CLI prompt includes:

- `Vault path`
- `Deterministic packet path`
- `Write semantic review JSON to`
- `Semantic scope`
- Scope instructions

Read the deterministic packet first. Its important fields are:

- `findings` — deterministic hard findings such as broken links, orphans, metadata issues, stale pages, provenance drift, visibility issues, fragmented tag clusters, promotion candidates, and lifecycle/confidence issues.
- `candidates.contradiction_candidates` — page pairs or clusters that deterministic signals suggest may contain conflicting claims.
- `candidates.synthesis_gap_candidates` — concept/entity pairs that co-occur often and may deserve synthesis.
- `page_registry` and `graph` — page metadata and resolved/broken edge evidence for targeted reading.

Treat deterministic `findings` as evidence, not something to recompute or restate.

## Scope Rules

Follow the `Semantic scope` supplied by the CLI:

- `one-hop`: You may read pages directly referenced by semantic candidates plus their one-hop wikilink neighbors. Do not roam beyond that set.
- `broad`: You may use the retrieval primitives from `llm-wiki/SKILL.md` across the vault, but still prefer targeted greps and section reads over whole-page reads.

In both modes, use the cheapest read that can answer the semantic question. Avoid full-page reads unless the candidate evidence is not enough.

## Review Tasks

### 1. Promote Contradictions

For each `contradiction_candidate`, decide whether the pages actually conflict.

Promote only when:

- Two or more pages make incompatible claims, recommendations, definitions, states, or assumptions.
- The disagreement is not already clearly framed as a known trade-off or open question.
- You can cite concise evidence from the involved pages.

Do not promote when:

- The pages merely use contrastive language.
- The claims differ by context, version, date, or scope and can both be true.
- The packet suggests a conflict but the relevant page text is absent or insufficient.

### 2. Promote Synthesis Gaps

For each `synthesis_gap_candidate`, decide whether the co-occurring concepts deserve a dedicated synthesis page.

Promote only when:

- The concepts/entities interact in a way not already captured by an existing `synthesis/` page.
- A synthesis page would add a cross-cutting insight, tension, decision frame, or reusable pattern.
- The co-occurrence is semantically meaningful, not just a shared tag or incidental project mention.

Do not promote when:

- Existing pages already explain the relationship well.
- The pair merely appears together without a real conceptual relationship.
- A better action is cross-linking, tag cleanup, or re-ingest rather than synthesis.

### 3. Repair Recommendations

Add concise repair recommendations only for semantic issues you promoted or hard deterministic findings whose next step is clear from the packet.

Examples:

- Run `a-inf synthesize` for a promoted synthesis gap.
- Run `a-inf research <topic>` to resolve a promoted contradiction.
- Run `a-inf cross-link` for a fragmented tag cluster or orphan set.
- Re-ingest or re-source pages with high ambiguity or stale verified status.

Do not recommend direct lifecycle changes unless the packet already shows a mechanical schema inconsistency. Human editors own lifecycle transitions.

## Output Contract

Write exactly one JSON object to the path named by `Write semantic review JSON to`. Do not print Markdown.

Required shape:

```json
{
  "status": "completed",
  "scope": "one-hop",
  "findings": {
    "contradictions": [],
    "synthesis_gaps": []
  },
  "repair_recommendations": [],
  "reviewed_candidate_ids": [],
  "warnings": []
}
```

Use the actual scope from the CLI prompt.

Each promoted contradiction should use this shape:

```json
{
  "candidate_id": "contradiction-1",
  "pages": ["concepts/a.md", "synthesis/b.md"],
  "explanation": "The pages make incompatible claims about ...",
  "evidence": [
    {"page": "concepts/a.md", "summary": "Claims ..."},
    {"page": "synthesis/b.md", "summary": "Claims ..."}
  ],
  "confidence": "high"
}
```

Each promoted synthesis gap should use this shape:

```json
{
  "candidate_id": "synthesis-gap-1",
  "pair": ["concepts/a.md", "entities/b.md"],
  "explanation": "A synthesis page would add ...",
  "evidence_pages": ["projects/x/x.md", "references/y.md"],
  "suggested_title": "A × B",
  "confidence": "medium"
}
```

`confidence` must be one of `high`, `medium`, or `low`.

`reviewed_candidate_ids` must include every candidate id you inspected, including candidates you declined to promote.

Put non-fatal issues in `warnings`, such as unreadable pages, ambiguous candidate evidence, or scope limits that prevented review.

## Hard Constraints

- Do not mutate the deterministic packet.
- Do not write anything except `semantic_review.json`.
- Do not append to `log.md`; the CLI does that.
- Do not duplicate deterministic hard findings into semantic findings.
- Do not fabricate evidence. If the page text does not support promotion, leave the candidate unpromoted and optionally add a warning.
