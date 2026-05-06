---
name: wiki-tags
description: >
  Review and plan tag taxonomy maintenance for an a-inf Obsidian wiki. Use this skill
  behind `a-inf tags` when the CLI has produced a deterministic tag audit packet and
  needs editable JSON suggestions for canonicalization, unknown tags, over-tagged pages,
  visibility tag issues, or taxonomy additions.
---

# Wiki Tags — Tag Taxonomy Planning

You review deterministic tag audit packets and write an editable tag plan. The CLI owns all filesystem edits.
Do not edit wiki pages, `_meta/taxonomy.md`, `index.md`, `log.md`, or `hot.md` directly.

## Before You Start

1. Read the deterministic packet path from the prompt.
2. Read the packet JSON first. It includes:
   - `tag_plan_path` — where to write the editable JSON plan
   - `taxonomy` — parsed canonical tags, aliases, and a bounded taxonomy excerpt
   - `page_registry` — page titles, categories, summaries, and current tags
   - `findings` — alias tags, unknown tags, duplicate tags, malformed tags, over-tagged pages, untagged pages, and visibility issues
3. If needed, read `_meta/taxonomy.md`, `index.md`, or narrowly relevant pages for context. Prefer packet data over broad vault reads.

## Contract

Write exactly one JSON object to `tag_plan_path`:

```json
{
  "version": 1,
  "status": "completed",
  "decisions": [
    {
      "action": "update",
      "page": "concepts/example.md",
      "expected_tags": ["current-tag"],
      "proposed_tags": ["canonical-tag"],
      "reason": "Short rationale."
    },
    {
      "action": "skip",
      "page": "concepts/ambiguous.md",
      "expected_tags": ["unclear"],
      "reason": "Needs human judgment."
    },
    {
      "action": "add_taxonomy_tag",
      "tag": "new-canonical-tag",
      "aliases": ["old-tag"],
      "section": "Suggested Tags",
      "reason": "Used across multiple pages and not covered by the existing taxonomy."
    }
  ],
  "warnings": []
}
```

The user can edit this plan before running `a-inf tags --fix`. The CLI validates the edited plan before applying any changes.

## Decision Rules

- Prefer canonical tags from the packet taxonomy.
- Replace explicit aliases with their canonical forms.
- Deduplicate tags.
- Normalize only clear malformed variants, such as `Bad Tag` to `bad-tag`, when the normalized form is semantically obvious.
- Keep at most 5 non-visibility tags per page.
- Prefer broad reusable tags over narrow one-off tags.
- For unknown tags used on 2+ pages, usually propose `add_taxonomy_tag` unless an existing canonical tag clearly covers them.
- For unknown tags used once, prefer mapping to a clear canonical tag or `skip` when uncertain.
- For over-tagged pages, drop the least descriptive/redundant tags only when the page title, summary, and current tags make the choice clear; otherwise use `skip`.

## Visibility Tags

`visibility/` tags are system tags, not taxonomy tags:

- `visibility/public`
- `visibility/internal`
- `visibility/pii`

Rules:

- They do not count toward the 5-tag limit.
- A page may have at most one visibility tag.
- Preserve visibility tags unless correcting invalid or duplicate visibility tags.
- Never add `visibility/internal` merely because content is technical.
- Do not propose `add_taxonomy_tag` for any `visibility/` tag.

## Validation Constraints

The CLI will reject invalid plans. Ensure every `update` or `skip` decision:

- Uses a page path that appears in `page_registry`.
- Includes `expected_tags` exactly as shown in `page_registry[page].tags`.
- Uses lowercase/kebab-case tags matching `^[a-z0-9][a-z0-9-]*(/[a-z0-9][a-z0-9-]*)?$`.
- Has no duplicate `proposed_tags`.
- Has no more than 5 non-visibility `proposed_tags`.
- Has at most one visibility tag, and only from the approved set.

Every `add_taxonomy_tag` decision must use a valid non-visibility tag. Aliases must also be valid non-visibility tags.

## Output Guidance

- Include decisions only for meaningful changes or explicit skips that explain ambiguity.
- Keep reasons short and concrete.
- Put uncertainty in `warnings` or `skip` decisions.
- Do not include prose outside the JSON file.
