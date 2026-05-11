---
name: wiki-ideate
description: >
  Write durable idea handoff packets for `a-inf ideate`. The CLI gathers bounded wiki context,
  chooses an `ideas/<slug>.md` output path, and asks Codex to write exactly one Markdown artifact.
---

# Wiki Ideate - Idea Packet Authoring

You are authoring a portable idea packet for another agent to explore or implement in a separate
repository. The downstream agent is trusted to inspect that repository, choose code changes, and
design experiments. Your job is to make the idea precise enough to transfer.

## Inputs

The CLI prompt includes:

- `Vault path`
- `Deterministic packet path`
- `Write Markdown idea packet to`

Read the deterministic packet first. It contains the raw idea, explicit wiki context, auto-selected
nearby context, support context from `AGENTS.md`, `hot.md`, and `index.md`, and the exact output path.

Treat wiki context as evidence. Preserve distinctions between:

- what the user explicitly asked for
- what the wiki says
- what you are inferring

## Output Contract

Write exactly one Markdown file at the requested output path. Do not write any other file.

The file must start with frontmatter:

```yaml
---
title: "Idea: <concise name>"
tags: [a-inf]
created: <ISO-8601 timestamp>
updated: <ISO-8601 timestamp>
sources:
  - "<wiki path or context source when applicable>"
---
```

If no wiki sources materially shaped the packet, use `sources: []`.

Use this body structure:

```markdown
# Idea: <concise name>

## Raw Idea
<Preserve the user's idea faithfully.>

## Distilled Claim
<A crisp falsifiable hypothesis, mechanism, or design claim.>

## Relevant Context
<Only context that materially changes the idea. Cite wiki pages with links.>

## Mathematical Sketch
<Definitions, notation, assumptions, equations, invariants, complexity, and a toy case when useful.>

## Agent Handoff
<What a future repo agent needs to understand before exploring or implementing. Keep this strategic,
not a repo-specific task list.>

## Open Questions
<Uncertainties the downstream agent should resolve in the target repo or by experiment.>
```

## Quality Bar

- The packet should be useful without the current conversation.
- The math section should be as formal as the idea permits; if formal math is not applicable, state
  the closest operational model, variables, invariants, and success criteria.
- Use `$...$` for inline math and `$$...$$` for display math. Do not emit `\[` and `\]` display
  delimiters in the generated Markdown packet.
- Avoid over-prescribing implementation steps. The downstream repo agent owns repo-specific planning.
- Use `^[inferred]` for non-obvious synthesized claims and `^[ambiguous]` where the packet context is thin.

## Hard Constraints

- Do not update `.manifest.json`, `index.md`, `log.md`, `hot.md`, QMD, or normal wiki pages.
- Do not create concept/entity/reference/synthesis pages.
- Do not omit `tags: [a-inf]`.
- Do not include instructions that tell the downstream agent to trust the packet over repository facts.
