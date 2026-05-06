---
name: wiki-insights
description: >
  Explain deterministic a-inf graph insights. Use this skill only as the bounded LLM
  explanation layer for `a-inf insights`; the CLI computes graph facts, writes
  `_insights.md`, appends `log.md`, and syncs QMD.
---

# Wiki Insights - Explanation Layer

`a-inf insights` is determinism-dominant. Python builds the page registry, resolves wikilinks,
computes graph metrics, writes `_insights.md` with the `a-inf` managed-file tag, appends `WIKI_INSIGHTS` to `log.md`, and refreshes QMD
after successful writes. Codex may only add short explanations to deterministic items.

## CLI Flow

1. The CLI reads config from `.a-inf/config.toml`, `~/.obsidian-wiki/config`, or `.env`.
2. The CLI reads the vault files directly and builds `.a-inf/runs/insights-*/packet.json`.
3. If there are fewer than 20 content pages, the CLI skips without writing `_insights.md`.
4. If `--no-codex` is set, the CLI writes a complete deterministic report with default reason labels.
5. Otherwise Codex receives the deterministic packet and writes only `explanations.json`.
6. Python validates explanations, merges accepted strings into `_insights.md`, appends `log.md` unless
   `--no-log` is set, and syncs QMD when the sandbox is writable.

## Deterministic Owner

Do not change or invent:

- page paths, titles, tags, categories, aliases, or summaries
- incoming/outgoing counts
- anchor, bridge, cohesion, surprising-connection, orphan, cluster, delta, or question rankings
- graph snapshots or graph deltas
- wiki files such as `_insights.md`, `log.md`, `hot.md`, `index.md`, or `.manifest.json`

Unresolved wikilinks are graph-health signals only; they are not hubs. `_insights.md` is regenerable
and includes a compact `GRAPH_SNAPSHOT` comment for the next deterministic diff.

## Codex Task

Read the packet path in the prompt. Write exactly one JSON object to the requested explanation path:

```json
{
  "version": 1,
  "status": "completed",
  "explanations": [
    {
      "id": "anchor:concepts/example.md",
      "explanation": "Short explanation grounded in the deterministic packet."
    }
  ],
  "warnings": []
}
```

Rules:

- Use only IDs from `explainable_item_ids`.
- Keep each explanation under 280 characters.
- Explain why the deterministic item matters; do not restate every count mechanically.
- Use `warnings` for packet limitations or skipped IDs.
- If an item cannot be explained from the packet, omit it.

## Useful Packet Sections

- `anchors`: top hubs, with incoming/outgoing counts and deterministic notes.
- `bridges`: pages connecting otherwise separated tag clusters.
- `tag_cohesion`: cohesive and fragmented tag clusters.
- `surprising_connections`: scored cross-category wikilinks with deterministic reason labels.
- `orphan_adjacent`: dead ends linked from top hubs.
- `rough_clusters`: anchor pages grouped by dominant tag.
- `delta`: page/link changes since the previous graph snapshot.
- `questions`: structure-derived questions.

## Flags

- `--no-codex`: skip this explanation layer and use deterministic labels.
- `--print-prompt`: create the deterministic packet and print the Codex prompt.
- `--json`: print the final CLI report as JSON.
- `--no-log`: do not append `WIKI_INSIGHTS` to `log.md`.
