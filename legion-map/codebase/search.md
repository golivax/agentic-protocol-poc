# Codebase Map — Semantic Search Protocol

This document tells Legion commands (and any consumer) how to query the map dataset
and then read source files before acting. Semantic search here is **retrieval over map
metadata plus source reads** — no embeddings, vector DBs, API keys, or external services.

## Required Artifacts

- `.planning/CODEBASE.md` — human-readable architecture, risks, conventions, runbook.
- `.planning/codebase/index.jsonl` — one JSON object per retrievable chunk.
- `.planning/codebase/symbols.json` — entry points, routes, apis, modules, tests, config,
  dependencies, ownership, risk_areas.
- `.planning/config/directory-mappings.yaml` — directory category mappings.

If any are missing, the dataset is `partial` — run `/legion:map --refresh`.

## Query Planning (Section 18.1)

Normalize a natural-language query or command context into:

```
query = {
  terms:        important nouns/verbs/feature/technology names,
  path_hints:   explicit files or directories mentioned,
  symbol_hints: classes/functions/routes/components mentioned,
  domain_hints: likely domains (engine, planner, state, checks, gates, fanout, api, dist)
}
```

## Retrieval Order (Section 18.2)

1. Search explicit **path hints** in `index.jsonl` (`path` field) and `symbols.json`.
2. Search **symbol hints** in `symbols.json` (`entry_points`, `routes`, `apis`, `modules`).
3. Search **terms and aliases** in `index.jsonl` (`keywords`, `aliases`, `summary`).
4. Search **CODEBASE.md** section headings for broad architecture context.
5. **Read the original source files** for the top matches before writing plans, review
   findings, or code changes.

Use `rg`/Grep over `index.jsonl` and `symbols.json`, e.g.:

```bash
rg -i "cas|state|advance" .planning/codebase/index.jsonl
rg -i "\"kind\": \"route\"" .planning/codebase/symbols.json
```

## Ranking (Section 18.3)

Rank matches by: exact path/symbol match → keyword/alias overlap → same domain as the
command context → risk level and fan-in relevance → recency (git hotspot data). Return at
most 5 primary chunks and 5 "read next" paths unless broader analysis is requested.

## Consumer Safety Rules (Section 18.6)

- Do **not** treat chunk summaries as source of truth for code edits — read the source.
- Do **not** cite stale map data as current without checking freshness (see the
  `source_fingerprint` / `generated_at` in CODEBASE.md; stale threshold is 30 days).
- Do **not** load the entire index into an agent prompt when a targeted query is enough.
- If query results conflict with current source files, **current source wins** and the map
  should be refreshed.

## Example — `/legion:map --query "how does state advance and get pushed?"`

```markdown
## Map Search Results

| Rank | Chunk | Path | Lines | Kind | Why it matched |
|------|-------|------|-------|------|----------------|
| 1 | map:engine-advance:001 | .github/agent-factory/engine/advance.py | 1-720 | module | symbol "advance"; term "CAS-push"; sole state writer |
| 2 | map:engine-lib:001 | .github/agent-factory/engine/lib.py | 1-1703 | module | symbol "cas_push"; term "state"; decide fold |
| 3 | map:engine-next:001 | .github/agent-factory/engine/next.py | 1-794 | module | term "action"; planner emits the advance command |

### Read Next
- `.github/agent-factory/engine/advance.py` lines 1-720 (the write + CAS + publish logic)
- `.github/agent-factory/engine/lib.py` (grep `cas_push`, `decide`)
- `docs/HOW-IT-WORKS.md` (design rationale for the state-machine-in-git model)
```
