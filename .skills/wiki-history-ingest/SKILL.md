---
name: wiki-history-ingest
description: >
  Codex history ingest entrypoint. Use this when the user says "$wiki-history-ingest codex",
  "/wiki-history-ingest codex", asks to ingest Codex history, or asks to mine local Codex sessions
  without naming the underlying skill.
---

# Codex History Ingest Router

This is a thin compatibility router for **Codex history sources only**. It does not replace `wiki-ingest` for documents or `data-ingest` for generic exports.

## Routing

Route directly to `codex-history-ingest` when the user:

- Invokes `$wiki-history-ingest codex`
- Invokes `/wiki-history-ingest codex`
- Says "import my Codex history"
- Provides `~/.codex`, `session_index.jsonl`, `history.jsonl`, or `sessions/**/rollout-*.jsonl`
- Asks to mine local Codex sessions or Codex transcript logs

If the user asks to ingest "agent history" without specifying a source, interpret it as Codex history in this repo and route to `codex-history-ingest`.

## Execution Contract

- Execute `codex-history-ingest` exactly.
- Do not duplicate parsing or write logic here.
- Leave manifest, index, log, and hot-cache update semantics to `codex-history-ingest`.

## UX Convention

- Use `wiki-ingest` for documents and staged content.
- Use `data-ingest` for generic exports, logs, and transcripts.
- Use `wiki-history-ingest` or `codex-history-ingest` for local Codex sessions.
