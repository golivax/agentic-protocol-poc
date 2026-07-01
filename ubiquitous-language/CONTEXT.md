# Agentic Protocol Engine

A generic, recursive state machine that drives sandboxed AI agents through a
declared, multi-step process on GitHub. The process is declared as data; one
engine interprets it and advances durable state only when deterministic checks
pass. This glossary is the ubiquitous language for that engine and the protocols
it runs.

## Language

### Protocol shape

**Protocol**:
A declared state machine — the ordered nodes, checks, transitions, and gates that
define one process, expressed as data in a `protocol.json`. Interpreted by the
Engine; never hand-written as workflow YAML.
_Avoid_: pipeline, workflow (a "workflow" here is the GitHub-Actions artifact, not the protocol)

**Engine**:
The generic, protocol-agnostic interpreter that plans, dispatches, checks, and
advances a Protocol. It contains no protocol-specific logic — it reads the
Protocol's identity and structure from data.
_Avoid_: driver, runner, orchestrator (the Orchestrator is a distinct component)

**Node**:
One structural unit of a Protocol's sequence, identified by an `id` and a `kind`
(agent · fanout · join · gate · merge). The single recursive unit the Engine
sequences over at any depth.
_Avoid_: state, step, phase — even though the DSL key is `states[]`, "state" is
reserved for the durable state file, and "phase" for a top-level node

**Agent**:
A node kind that dispatches one sandboxed LLM step, whose only output is an
Evidence artifact the checks verify. Also the sandboxed process itself (e.g. the
grumpy or security reviewer).
_Avoid_: bot, model, reviewer (those name specific agent personas, not the concept)

**Fanout**:
A node that splits into parallel Legs — either a static list or a runtime-derived
set — to be rejoined later by a Join.
_Avoid_: split, parallel, matrix

**Leg**:
One parallel arm of a Fanout at runtime: a flat Agent arm or a nested
Sub-pipeline, each with its own iterate loop and state file. The DSL declares
static legs under the key `branches[]`.
_Avoid_: branch (reserve "branch" for a git branch; a Leg is never one), thread, worker

**Join**:
The AND-barrier node that waits for every Leg of one Fanout to reach a terminal
state, then applies a success `policy` (`all` · `any` · `quorum`) to decide the
aggregate outcome.
_Avoid_: barrier, gather, merge (Merge is a different node)

**Gate**:
A node that pauses the process for a human: an **approval gate** (a human
`/approve`s) or a **data gate** (an Agent poses questions and a human `/answer`s).
Opening a gate ends the run; the reply is a fresh wake-up.
_Avoid_: pause, checkpoint, approval (approval is one flavor of gate)

**Merge**:
A post-Join node that reduces the joined Legs into one output via a trusted hook.
_Avoid_: combine, reduce, join (the combine hook in some protocols is a Merge)

**Sequence / Sub-pipeline**:
A Sequence is an ordered list of nodes; the Protocol root is one. A Sub-pipeline
is a Fanout Leg that is itself a Sequence rather than a single Agent, enabling
arbitrary nesting.
_Avoid_: block, group, subprotocol

**Node path**:
The variable-length coordinate that names any node at any depth, from which the
Engine derives that node's state-file path. The one address that unifies
single-agent, fan-out, and deeply-nested shapes.
_Avoid_: node id (an id is one segment; the path is the full address)

**Terminal**:
A node's finished disposition — `done` (checks passed within the iteration
budget) or `failed` (they did not). The two terminals are implicit; a Protocol
points at them but never declares them.
_Avoid_: final, end, complete

### The contract: evidence and checks

**Evidence**:
The single structured artifact (`evidence.json`) an Agent must emit to satisfy
its schema — the whole contract for an Agent node. The Agent can affect the world
only through it.
_Avoid_: output, result, report

**Evidence schema**:
The JSON Schema declaring exactly what an Agent's Evidence must contain — the
enumerable contract the checks verify. Prose in the Agent's prompt is only
guidance on how to fill it.
_Avoid_: spec, template

**Rubric**:
The enumerable grid an Agent must cover completely — canonically categories ×
changed files. Choosing the rubric is the act of decomposing a judgment task into
something a check can gate.
_Avoid_: matrix, checklist, criteria

**Cell**:
One unit of the Rubric (one category × one file) that must carry exactly one
verdict — zero (skipped) and two-or-more (padded) both fail coverage.
_Avoid_: entry, item

**Finding**:
A concrete issue an Agent reports for a Cell, carrying verbatim `existing_code`
and a `side`/`line` anchor into the diff. Contrast a negative attestation.
_Avoid_: issue, comment, result

**Negative attestation**:
A legal "none-found" verdict that must still carry the `examined` identifiers the
Agent inspected — so a check can confirm the Agent actually read the code. Turns
silent skipping into a falsifiable claim.
_Avoid_: pass, empty, no-op

**Examined**:
The list of identifiers an Agent attests it inspected when it records
"none-found" — the trace that makes a negative attestation checkable.
_Avoid_: checked, seen

**Anchor**:
A Finding's pointer into the independently-fetched diff: verbatim `existing_code`
plus a `side` (`RIGHT`/`LEFT`) and `line`, optionally a `start_line` range. A
passing anchor is a valid GitHub review position.
_Avoid_: reference, location, trace (a trace is the broader evidence-to-code link)

**Verdict**:
Overloaded across three layers, kept distinct by context: (1) a **Cell verdict**
(`issues-found`/`none-found`) the Agent records per Rubric cell; (2) a **check
verdict** (`{check, pass, feedback}`) a Check emits; (3) the **review verdict**
axis (APPROVE / CHANGES_REQUESTED) a publish hook concludes. Name the layer when
ambiguous.
_Avoid_: conclusion, result (too vague to disambiguate the three)

**Check**:
A deterministic, credential-free executable that verifies the *form* of Evidence
against independently-derived ground truth (re-fetching the diff itself). It
verifies form, never substance, and always exits 0.
_Avoid_: test, validator, gate (a Gate is a human pause, not a check)

**Verification**:
Judging the *substance* of a Finding (is it correct?) — the job of a future LLM
judge or a human Gate, explicitly **not** a Check. The form/substance split is
load-bearing.
_Avoid_: check (a Check does form; verification does substance)

**Feedback**:
A failed Check's specific, actionable message, injected into the next iteration's
prompt so the Agent fixes exactly what was rejected.
_Avoid_: error, message, hint

**Iteration**:
One bounded iterate-with-feedback round: dispatch the Agent, run its checks, and
either finish or re-dispatch with feedback, up to `max_iterations` before the
node is `failed`.
_Avoid_: retry, attempt, loop

**Hook**:
A trusted, protocol-authored executable the Engine runs with credentials —
distinct from a sandboxed Check. Flavors: a **publish hook** (posts the review /
check run), a **conclude hook** (may return `blocked`), a **merge hook** (reduces
joined legs), and an **expander** (derives a dynamic Fanout's legs at runtime).
_Avoid_: script, plugin, callback

**Manifest**:
The durable record the Engine writes when a dynamic Fanout expands — the frozen
list of legs (`{id, key, item}`) that both the Join and a Merge read, so they can
never disagree on cardinality.
_Avoid_: list, index

### Execution and durable state

**Transition**:
One movement of the state machine: a single workflow run re-derives state, acts,
writes new state, and exits. There is no long-lived driver — the whole Protocol is
a chain of independent transitions.
_Avoid_: step, run, advance

**Instance**:
One live run of a Protocol against one subject, addressed by an **instance key**
(`pr-<N>` for a PR, or `ref-<ref>`/`ui-<id>` for a dispatch). The subject — a PR
or a branch — is the unit of existence; workflow runs are heartbeats that advance
it.
_Avoid_: session, job, execution

**State**:
The durable per-node YAML file on the `agentic-state` branch that *is* the source
of truth — the current node, iteration, gate history, and audit trail. Events
never carry state; everything load-bearing is re-derived from here.
_Avoid_: status, node (a node is structural; state is the durable data)

**Compare-and-swap (CAS)**:
The only way state advances: a fast-forward push to `agentic-state`, with git
rejecting non-fast-forward pushes to resolve concurrent writers. The state branch
is never force-pushed.
_Avoid_: commit, save, overwrite

**Event / Command / Wake-up**:
A GitHub **event** (comment, push, dispatch) is only a **wake-up** telling the
Engine to look. The Orchestrator translates it into one of a fixed set of
**commands** (`start` · `reset` · `continue` · `override` · `resolve-gate` ·
`answer`) that actually drive the Engine.
_Avoid_: message, signal (be specific: event vs command vs wake-up)

**Trigger**:
A Protocol's declared mapping (in `triggers[]`) from a GitHub event to a command
— the entry points the Router scans to decide which Protocol an event belongs to.
_Avoid_: event, hook

**Correlation id (cid)**:
The unique token the dispatch stamps into an Agent run's title so the Engine can
later resolve *the exact run it started*, failing loudly rather than guessing by
recency. What makes concurrent instances safe.
_Avoid_: run id, key, token

**Status comment**:
The single PR/issue comment the Engine re-renders on every transition into a
live checklist of iterations and outcome — the human-facing view of the run.
_Avoid_: report, log, update

**Check run**:
A GitHub check-run the Engine publishes to gate merge — a per-Leg one
(informational) and an aggregate one named after the Protocol (the required gate).
_Avoid_: check (a Check is the deterministic verifier; a check run is the GitHub gate object)

**Process vs Verdict (the two axes)**:
Two orthogonal outcomes the Engine keeps separate: **process** (`done`/`failed` —
did checks pass within the iteration budget) versus **verdict** (APPROVE /
CHANGES_REQUESTED — what a successful review concluded). A Join cares only about
the process axis.
_Avoid_: conflating a requested-changes review (process success) with a failed run

**Trust zone**:
One of four credential-isolated jobs per iteration (engine-pre · agent ·
checks · engine-post). The invariant: the Engine and the Agent never share a job
or a credential; agent-influenced strings never reach a credentialed shell.
_Avoid_: stage, layer, phase

**Blocked**:
A conclude hook's disposition that halts the pipeline pending a human `/override`
— distinct from `failed` (checks exhausted) and from a Gate (a planned pause).
_Avoid_: failed, paused, halted

### Components and neighbors

**Orchestrator / Router**:
The workflow that receives every GitHub event, routes it to the owning Protocol
by scanning triggers, maps the event to a command, and invokes the reusable
Engine. All trigger policy lives here, never in the Engine.
_Avoid_: engine, dispatcher

**Agent factory**:
The self-contained, vendored unit (`.github/agent-factory/`) holding the Engine
plus the Protocol library — the thing the installer drops into a target repo.
_Avoid_: framework, library

**gh-aw (GitHub Agentic Workflows)**:
The upstream system that compiles a markdown agent into a sandboxed Action with
read-only credentials and schema-validated **safe-outputs**. The Engine reuses its
per-step sandbox; each Agent node is dispatched as a gh-aw workflow.
_Avoid_: agent, action

**porch**:
The upstream deterministic protocol engine (declared phases/checks/gates, git
state, a pure planner) whose control model this Engine **inverts** — here the
engine drives and the agent is dispatched, rather than the agent calling the
planner as a tool.
_Avoid_: engine (this project's Engine is the inversion of porch, not porch)

### The shipped protocols

**code-review**:
The production Protocol: preflight → a review Fanout (grumpy ∥ security) → Join →
human approval Gate. The reference example the engine exercises.

**recover-mental-model**:
A fully-automated Protocol that runs four parallel mental-model recovery methods
(legion ∥ codeset ∥ ubiquitous-language ∥ a socratic Sub-pipeline) → Join →
a combine Merge that publishes the results to an orphan branch.
_Avoid_: mind-map, onboarding (name the specific methods, not the umbrella)

**recover-mental-model-interactive**:
The interactive twin of `recover-mental-model`: the socratic answering step is a
human data Gate (answered on a dedicated issue) instead of an auto-answering
Agent.

**deep-review-stub**:
A deeply-nested Fanout/Sub-pipeline Protocol with stub agents, used to exercise
the recursive Engine at depth.
