---
name: wiki-insights
description: >
  Analyze the shape of the wiki graph. Use this skill when the user asks for "wiki insights",
  "what's central", "show me the hubs", "central pages", "cross-domain bridges", "what pages
  are most important", "what's connected", or "wiki structure". This is separate from deterministic
  `a-inf status`, which reports ingest state and deltas.
---

# Wiki Insights - Graph Analysis

You are analyzing the existing wiki graph to surface central pages, bridge pages, fragmented tag
clusters, and useful follow-up questions. This complements `wiki-lint` (which finds problems) by
surfacing interesting structure.

## Before You Start

1. Resolve configuration from `.a-inf/config.toml`, `~/.obsidian-wiki/config`, or `.env`.
2. Read `<vault path>/AGENTS.md` if it exists and apply owner-specific conventions.
3. Read `.manifest.json` to understand whether the vault has been ingested.
4. Skip `_archives/`, `_raw/`, `.obsidian/`, `.skills/`, `node_modules/`, `index.md`, `log.md`,
   `hot.md`, `_insights.md`, and `_meta/*` when building the graph.

## When to Skip

- Vaults with fewer than 20 content pages do not have enough graph structure. Tell the user and skip.
- After a fresh `wiki-rebuild`, wait until at least one ingest has happened.

## Build the Graph

Glob all content `.md` pages, extract every `[[wikilink]]`, and build:

- `incoming[page]` = count of other pages that link to this page
- `outgoing[page]` = count of pages this page links out to
- `tags[page]` = set of tags from frontmatter
- `category[page]` = directory prefix (`concepts/`, `entities/`, `skills/`, etc.)

Resolve wikilinks by page stem and relative vault path where possible. Keep unresolved targets in the
edge list only if they point to a plausible page name; note unresolved-heavy results as a graph-health
signal rather than treating them as real hubs.

## What to Compute

1. **Anchor pages (top hubs).** Rank all pages by incoming count, take the top 10, and show incoming
   and outgoing counts. Pages with high incoming and high outgoing are connector hubs. Pages with high
   incoming but zero outgoing are sink hubs and cross-linker candidates.

2. **Bridge pages.** Find pages that connect otherwise-disconnected tag clusters. For each page `P`,
   count pairs `(A, B)` where `A` links to `P`, `P` links to `B` (or the reverse), `A` and `B` share no
   tags, and `P` is the only path between their tag clusters within 2 hops. Rank by cross-cluster pair
   count and show the top 5.

3. **Tag cluster cohesion.** For each tag with at least 5 pages, compute:

   `cohesion = actual_links / (n * (n - 1) / 2)`

   where `actual_links` is the number of wikilinks between pages sharing that tag. Show the 5 most
   cohesive tags and the 5 most fragmented tags. Tags with cohesion below `0.15` are cross-linker targets.

4. **Surprising connections.** Score cross-category wikilinks:

   - `+3` if the linking page or claim is marked `^[ambiguous]`
   - `+2` if the linking page is marked `^[inferred]`
   - `+2` if categories are in different knowledge layers, such as `concepts` to `entities`
   - `+2` if the source page has 2 or fewer total links and the target has 8 or more incoming links

   Show the top 5 scored connections with a short reason for each.

5. **Orphan-adjacent suggestions.** List pages linked from a top-10 hub that have zero outgoing links.

6. **Rough clusters.** Group anchor pages by dominant tag using simple tag intersection.

7. **Graph delta since last run.** Read the `<!-- GRAPH_SNAPSHOT: ... -->` line at the bottom of the
   previous `_insights.md` if it exists. Compute new pages, removed pages, new wikilinks, removed
   wikilinks, newly connected pages, and pages that lost incoming links.

8. **Suggested questions.** Generate up to 7 deterministic, structure-derived questions. Prioritize:
   ambiguous claims, bridge nodes, isolated pages, and fragmented clusters.

## Output

Write the result to `_insights.md` at the vault root. Overwrite freely because it is regenerable. Embed
a compact graph snapshot as an HTML comment at the end so the next run can diff against it.

```markdown
# Wiki Insights - <TIMESTAMP>

## Anchor Pages (top 10 hubs)
| Page | Incoming | Outgoing | Note |
|---|---|---|---|
| [[concepts/transformer-architecture]] | 23 | 8 | connector hub |
| [[entities/andrej-karpathy]] | 17 | 0 | sink hub - cross-linker candidate |

## Bridge Pages (top 5)
| Page | Bridges | Cross-cluster pairs |
|---|---|---|
| [[concepts/exponential-growth]] | #ml <-> #economics | 4 pairs |

## Tag Cluster Cohesion
### Most cohesive (well-linked)
- **#ml** - 12 pages, cohesion 0.41
### Most fragmented (cross-linker targets)
- **#systems** - 7 pages, cohesion 0.06 - run cross-linker on this tag

## Surprising Connections (top 5)
- [[concepts/scaling-laws]] -> [[entities/gordon-moore]] - score 5
  - Reason: cross-layer (concepts <-> entities), marked ^[inferred]

## Orphan-Adjacent (dead-ends near hubs)
- [[concepts/foo]] - linked from 3 hubs, 0 outbound links

## Rough Clusters
- **#ml** - transformer-architecture, attention-mechanism, scaling-laws
- **#systems** - distributed-consensus, raft, paxos

## Graph Delta Since Last Run
- +3 new pages, +11 new wikilinks
- Newly connected: [[concepts/bar]], [[entities/baz]]
- Lost incoming links: [[references/old-paper]] (target may have been renamed)

## Questions Worth Asking
1. Resolve: What is the exact relationship between `scaling-laws` and `moore's-law`? (^[ambiguous] claim)
2. Explore: Why does `exponential-growth` bridge #ml and #economics?
3. Link: `references/foo.md` has no incoming links - what should reference it?
4. Audit: Should tag `#systems` be split? (cohesion 0.06, 7 pages)

<!-- GRAPH_SNAPSHOT: {"nodes":["concepts/foo","entities/bar"],"edges":[["concepts/foo","entities/bar"]]} -->
```

After writing the file, append to `log.md`:

```markdown
- [TIMESTAMP] WIKI_INSIGHTS anchors=10 bridges=N cohesion_checked=T surprising=5 questions=7 delta="+N pages +M links"
```
