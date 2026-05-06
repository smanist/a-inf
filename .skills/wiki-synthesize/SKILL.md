---
name: wiki-synthesize
description: >
  Semantic authoring layer for `a-inf synthesize`. The CLI deterministically discovers synthesis
  opportunities, builds an authoring packet, validates the returned JSON plan, and applies wiki edits.
  Use when the user says "synthesize my wiki", "find connections", "what concepts keep coming up
  together", "/wiki-synthesize", or after a large ingest when the vault has grown significantly.
---

# Wiki Synthesize - Semantic Authoring

`a-inf synthesize` is packet-first:

1. Python builds a deterministic synthesis packet from lint synthesis-gap candidates.
2. Codex reads that packet and this skill.
3. Codex writes a separate `synthesis_plan.json`.
4. The CLI validates the plan, creates synthesis pages, adds backlinks, rebuilds `index.md`, updates
   `hot.md`, appends `log.md`, updates manifest stats, and syncs QMD.

Do **not** edit wiki files, create markdown files, update frontmatter, add backlinks, append logs,
or mutate the deterministic packet. The CLI owns all filesystem changes.

## Inputs

The CLI prompt includes:

- `Vault path`
- `Deterministic packet path`
- `Write synthesis plan JSON to`

Read the deterministic packet first. Important fields:

- `candidates` - up to 5 synthesis opportunities selected for authoring.
- `opportunities` - skipped-but-interesting candidates for visibility only.
- `authoring_context` - bounded excerpts, summaries, tags, confidence, and Related sections for
  candidate pages and evidence pages.
- `existing_synthesis_pages` - nearby synthesis pages already present in the vault.
- `page_registry`, `graph`, `index_summary`, `hot`, `taxonomy`, and `agents` - supporting context.

Treat the packet as the source of truth for candidate IDs, page paths, titles, evidence pages,
link format, and deterministic target paths. Do not invent candidates outside the packet.

## Review Tasks

For each item in `candidates`, choose `create` or `skip`.

Create only when:

- The pair has a real conceptual relationship, not just incidental co-occurrence.
- A synthesis page would add a cross-cutting insight, tension, decision frame, or reusable pattern.
- Existing synthesis pages do not already capture the relationship.
- The packet provides enough evidence to write without fabricating.

Skip when:

- The pair merely shares a tag, project, or list of links.
- Existing pages already explain the relationship well.
- The evidence is too thin or ambiguous.
- The better action is fixlinking, tag cleanup, research, or re-ingest.

## Writing Guidance

For `create`, write only the `summary`, markdown `body`, optional `open_questions`, and optional
`note`. The CLI will generate title, path, frontmatter, sources, provenance, confidence, lifecycle,
Related backlinks, special-file updates, and QMD sync.

The body should be useful as a final synthesis page body. Prefer this structure unless the packet
strongly suggests a better one:

```markdown
## The Connection

What makes these two concepts worth synthesizing together.

## Where They Co-occur

The pages and contexts where both appear, grounded in packet evidence.

## Cross-cutting Insight

The conclusion visible only when the concepts are considered together. Mark synthesized claims with
^[inferred].

## Tensions and Trade-offs

Where the concepts pull in different directions, including unresolved contradictions or scope limits.
Mark uncertain claims with ^[ambiguous].

## Open Questions

Questions surfaced by the synthesis.
```

The CLI adds a top-level heading if missing and appends a Related section if missing. You may include
those sections yourself when useful, but do not include frontmatter.

## Output Contract

Write exactly one JSON object to the path named by `Write synthesis plan JSON to`. Do not print
Markdown and do not edit any wiki files.

Required shape:

```json
{
  "version": 1,
  "status": "completed",
  "decisions": [],
  "hot_update": {
    "recent_activity": [],
    "active_threads": []
  },
  "warnings": []
}
```

Each `create` decision must use this shape:

```json
{
  "candidate_id": "synthesis-gap-1",
  "action": "create",
  "summary": "Under 200 characters.",
  "body": "## The Connection\n\n...",
  "open_questions": ["Question surfaced by the synthesis."],
  "note": "Optional concise rationale."
}
```

Each `skip` decision must use this shape:

```json
{
  "candidate_id": "synthesis-gap-2",
  "action": "skip",
  "note": "Existing pages already cover the relationship."
}
```

`hot_update.recent_activity` should briefly summarize created syntheses. `hot_update.active_threads`
should list the most important open questions from created pages. Put non-fatal issues in `warnings`,
such as thin evidence, unclear candidate framing, or candidates that should be handled by another
workflow.

## Hard Constraints

- Do not mutate the deterministic packet.
- Do not write anything except `synthesis_plan.json`.
- Do not include `path`, `target_path`, `frontmatter`, `sources`, `created`, or `updated` in decisions.
- Do not fabricate evidence beyond the packet.
- Do not create pages for candidates you would not want permanently added to the wiki.
- A synthesis page that only summarizes its sources is not useful; it must add a relationship-level
  insight.
