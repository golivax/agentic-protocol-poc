# Protocol Visibility REST API — Backlog

Deferred work for the read-only protocol-visibility REST API. The approved design
lives in `docs/superpowers/specs/2026-06-24-rest-api-design.md`.

## Deferred endpoints

- **History timeline** — `GET /protocols/{protocol}/instances/{pr}/history`
  Full per-instance transition log: every `history[]` entry (`iteration`,
  `agent_run_id`, `checks{}`, `feedback`) across the instance's state files, in order.
  Additive over `/status` (which is a current-state projection). Deferred during
  brainstorming on 2026-06-24 — add if a client needs the raw event log rather than
  the projected current status.

## Deferred capabilities

- **Caching / synced store** — the design reads GitHub at request time. If `/stats`
  (one blob fetch per instance) or `/gates` (full scan) get slow at scale, introduce a
  cache or a small synced store. First candidate to cache: per-instance head state for
  `/stats`.

- **Billed-minute precision** — `action_minutes_approx` is wall-clock, not billed
  minutes. If precise billing is needed, sum the Actions timing API per run (costlier).

## Future direction (tracked in the spec)

- **Write (POST) endpoints** — command-initiator pattern only (never direct state-branch
  writes). See the "Future: write (POST) endpoints" section of the design spec for the
  hard constraint and auth implications.
