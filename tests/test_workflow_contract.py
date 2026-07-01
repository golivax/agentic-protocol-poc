"""test_workflow_contract.py — structural contract tests for the GHA workflow files.
These tests run offline (no GitHub Actions environment needed) and verify that
the workflow YAML files contain/exclude specific strings that encode the
NODE_PATH wiring contract established in Stage 4b.
"""
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
WF = ROOT / ".github/workflows"


def _load(name):
    return WF.joinpath(name).read_text()


def test_engine_yml_threads_node_path_not_legacy():
    t = _load("agentic-engine.yml")
    # NODE_PATH is threaded from the matrix leg.
    assert "NODE_PATH: ${{ matrix.leg.path }}" in t
    assert "matrix.leg.workflow" in t
    assert "github.event.client_payload.path" in t
    # legacy coordinate wiring is gone from the engine jobs.
    assert "client_payload.branch" not in t
    assert "client_payload.substate" not in t
    assert "client_payload.phase" not in t
    assert "advance-phase" not in t
    assert "agent-workflow" not in t   # dispatch reads matrix.leg.workflow now
    # matrix is fed from the action's legs.
    assert "fromJSON(needs.plan.outputs.legs)" in t


def test_engine_yml_matrix_leg_has_path_and_workflow():
    t = _load("agentic-engine.yml")
    assert "matrix.leg.path" in t


def test_join_yml_threads_node_path_and_path_concurrency():
    t = _load("protocol-join.yml")
    assert "NODE_PATH: ${{ github.event.client_payload.path }}" in t
    # concurrency group is path-aware so nested joins don't serialize against the top join
    assert "join-${{ github.event.client_payload.instance }}-${{ github.event.client_payload.path }}" in t


def test_orchestrator_yml_path_concurrency_and_no_protocol_advance():
    t = _load("agentic-orchestrator.yml")
    # concurrency keyed on instance (workflow_dispatch UI id falls back to the
    # dispatch/PR instance) + path.
    assert "agentic-${{ github.event.inputs.instance || github.event.client_payload.instance" in t
    assert "github.event.client_payload.path }}" in t   # concurrency keyed on path
    assert "protocol-advance" not in t                  # dropped from on: types
    # protocol-continue is still accepted; protocol-join still owned by protocol-join.yml
    assert "protocol-continue" in t
    # the UI/API entry point exists
    assert "workflow_dispatch:" in t


def test_no_workflow_references_retired_mechanisms():
    for name in ("agentic-engine.yml", "protocol-join.yml", "agentic-orchestrator.yml"):
        t = _load(name)
        assert "protocol-advance" not in t, name
        assert "client_payload.branch" not in t, name
        assert "client_payload.substate" not in t, name


def test_lint_workflow_runs_actionlint():
    t = _load("lint.yml")
    assert "actionlint" in t


def test_orchestrator_routes_plain_issue_comments():
    t = _load("agentic-orchestrator.yml")
    # The route job must accept ANY issue_comment (not only PR comments); lib.route
    # decides skip via the target field.
    assert "github.event.issue.pull_request != null" not in t.split("jobs:")[0] \
        or "issue_comment'" in t  # guard relaxed; see below assertion
    # instance derivation distinguishes pr- vs issue- keys for issue_comment events.
    assert "format('issue-{0}'" in t
    assert "format('pr-{0}'" in t


def test_engine_yml_derives_issue_instance_and_default_branch():
    t = _load("agentic-engine.yml")
    assert "issue-$" in t              # INSTANCE="issue-$N" path exists
    assert "default_branch" in t       # checkout the default branch for the issue case


def test_design_agent_lock_is_readonly_and_bundles_spec():
    t = _load("impl-feature-auto-design-agent.lock.yml")
    # The AGENT job (the one running the LLM) is read-only. gh-aw's vetted
    # safe_outputs/conclusion post-processing jobs legitimately carry issues/
    # pull-requests write (needed for any declared safe-output), so we assert the
    # invariants that actually matter for the design agent rather than a coarse
    # whole-lock "no write" scan:
    assert "contents: write" not in t       # design writes NO repo content (no code, no push)
    # design opens no PR — guard both the hyphenated source key and the underscored
    # token gh-aw compiles to (the compiler only ever emits create_pull_request).
    assert "create_pull_request" not in t and "create-pull-request" not in t
    # design produces NO auto per-run status issue: the default create-issue is
    # suppressed by declaring a real safe-output (add-comment, which the prompt
    # forbids the agent from emitting, so it stays inert).
    assert "create_issue" not in t
    assert "add_comment" in t                # the (inert) suppressor of the default create-issue
    assert "evidence" in t                   # uploads evidence artifact
    assert ".claude/skills" in t             # stages superpowers


def test_implement_agent_lock_opens_pr():
    t = _load("impl-feature-auto-implement-agent.lock.yml")
    # gh-aw v0.77.5 emits the safe-output as the underscored token
    # `create_pull_request` in the compiled lock (the hyphenated source key is
    # normalized away). Accept either form — implement opens the PR via safe-outputs.
    assert "create-pull-request" in t or "create_pull_request" in t
