#!/usr/bin/env bash
# Tests for the data-driven, polyglot check runner (.github/engine/run-checks.sh).
set -euo pipefail
cd "$(dirname "$0")/.."
FX=tests/fixtures
RC=.github/engine/run-checks.sh
P=protocols/grumpy/protocol.json
PASS=0; FAIL=0
ok()  { PASS=$((PASS+1)); echo "ok: $1"; }
bad() { FAIL=$((FAIL+1)); echo "FAIL: $1"; }
chk() { if eval "$2"; then ok "$1"; else bad "$1"; fi; }

# --- happy path: 3 checks (2 bash + 1 python) resolve, run, all pass ---
OUT=$("$RC" "$P" review "$FX/evidence-complete.json" "$FX/diff-pr1.txt" "$FX/changed-files-pr1.txt")
chk "runner: 3 results"            '[ "$(jq -r ".results|length" <<<"$OUT")" = 3 ]'
chk "runner: all pass on complete" '[ "$(jq -r "[.results[].pass]|all" <<<"$OUT")" = true ]'
chk "runner: includes python rubric-coverage" 'jq -e ".results[]|select(.check==\"rubric-coverage\")" <<<"$OUT" >/dev/null'

# --- lazy evidence: rubric-coverage (python) fails, others pass, still aggregated ---
OUT=$("$RC" "$P" review "$FX/evidence-lazy.json" "$FX/diff-pr1.txt" "$FX/changed-files-pr1.txt")
chk "runner: lazy → rubric-coverage fails" '[ "$(jq -r ".results[]|select(.check==\"rubric-coverage\")|.pass" <<<"$OUT")" = false ]'
chk "runner: lazy → schema-valid passes"   '[ "$(jq -r ".results[]|select(.check==\"schema-valid\")|.pass" <<<"$OUT")" = true ]'

# Temp protocols must live alongside the real checks dir so resolution and the
# checks' own protocol.json lookup (checks/../protocol.json) both work.
TP=protocols/grumpy/.test-proto.json
trap 'rm -f "$TP"' EXIT

# --- unknown check name → synthesized not-found failure, run still completes ---
jq '.states[0].checks = [{"run":"does-not-exist","on_fail":"iterate"}]' "$P" > "$TP"
OUT=$("$RC" "$TP" review "$FX/evidence-complete.json" "$FX/diff-pr1.txt" "$FX/changed-files-pr1.txt")
chk "runner: unknown check → fail verdict" '[ "$(jq -r ".results[0].pass" <<<"$OUT")" = false ]'
chk "runner: unknown check → useful feedback" 'jq -r ".results[0].feedback" <<<"$OUT" | grep -q "no executable found"'

# --- explicit exec override resolves a specific file ---
jq '.states[0].checks = [{"run":"sv","exec":"checks/schema-valid.sh","on_fail":"iterate"}]' "$P" > "$TP"
OUT=$("$RC" "$TP" review "$FX/evidence-complete.json" "$FX/diff-pr1.txt" "$FX/changed-files-pr1.txt")
chk "runner: exec override runs the file" '[ "$(jq -r ".results[0].pass" <<<"$OUT")" = true ]'

# --- non-executable check file → fail verdict (not a crash) ---
SBX=$(mktemp -d); mkdir -p "$SBX/checks"
echo '{"name":"x","categories":[],"states":[{"id":"review","checks":[{"run":"noexec"}]}]}' > "$SBX/protocol.json"
printf '#!/usr/bin/env bash\necho "{}"\n' > "$SBX/checks/noexec.sh"   # intentionally not chmod +x
OUT=$("$RC" "$SBX/protocol.json" review "$FX/evidence-complete.json" "$FX/diff-pr1.txt" "$FX/changed-files-pr1.txt")
chk "runner: non-executable check → fail verdict" '[ "$(jq -r ".results[0].pass" <<<"$OUT")" = false ]'
chk "runner: non-executable → useful feedback" 'jq -r ".results[0].feedback" <<<"$OUT" | grep -q "not executable"'

# --- a check that crashes (non-zero exit) → fail verdict, run survives ---
printf '#!/usr/bin/env bash\nexit 3\n' > "$SBX/checks/noexec.sh"; chmod +x "$SBX/checks/noexec.sh"
OUT=$("$RC" "$SBX/protocol.json" review "$FX/evidence-complete.json" "$FX/diff-pr1.txt" "$FX/changed-files-pr1.txt")
chk "runner: crashing check → fail verdict" '[ "$(jq -r ".results[0].pass" <<<"$OUT")" = false ]'

# --- resolve_executable (shared resolver) direct unit checks ---
source .github/engine/lib.sh
PDIR="protocols/grumpy"
TAB=$'\t'   # split helper: resolve_executable returns "<kind>\t<rest>"
R=$(resolve_executable "$PDIR/checks" "schema-valid" "$PDIR" "")
chk "resolve: finds checks/schema-valid.sh" '[ "${R%%$TAB*}" = OK ] && grep -q "checks/schema-valid.sh" <<<"$R"'
R=$(resolve_executable "$PDIR/checks" "does-not-exist" "$PDIR" "")
chk "resolve: missing → ERR" '[ "${R%%$TAB*}" = ERR ]'
R=$(resolve_executable "$PDIR/checks" "ignored" "$PDIR" "checks/rubric-coverage.py")
chk "resolve: explicit exec resolves" '[ "${R%%$TAB*}" = OK ] && grep -q "rubric-coverage.py" <<<"$R"'

# --- branch-aware check list: BRANCH selects .branches[].checks ---
MG=protocols/multi-grumpy/protocol.json
OUT=$(BRANCH=grumpy "$RC" "$MG" review "$FX/evidence-complete.json" "$FX/diff-pr1.txt" "$FX/changed-files-pr1.txt")
chk "branch grumpy → 3 checks run"  '[ "$(jq -r ".results|length" <<<"$OUT")" = 3 ]'
OUT=$(BRANCH=security "$RC" "$MG" review "$FX/evidence-complete.json" "$FX/diff-pr1.txt" "$FX/changed-files-pr1.txt")
chk "branch security → 2 checks run (no rubric-coverage)" \
  '[ "$(jq -r ".results|length" <<<"$OUT")" = 2 ] && ! jq -e ".results[]|select(.check==\"rubric-coverage\")" <<<"$OUT" >/dev/null'

# security branch's params.categories is ["security"], so schema-valid must reject
# evidence that carries any other category (the deterministic precision win that
# replaces a prompt-only rule). evidence-complete.json contains naming/perf/etc.
OUT=$(BRANCH=security "$RC" "$MG" review "$FX/evidence-complete.json" "$FX/diff-pr1.txt" "$FX/changed-files-pr1.txt")
chk "branch security → schema-valid rejects non-security category" \
  '[ "$(jq -r ".results[]|select(.check==\"schema-valid\")|.pass" <<<"$OUT")" = false ] && jq -r ".results[]|select(.check==\"schema-valid\")|.feedback" <<<"$OUT" | grep -q "illegal category"'

# --- params forwarding: CHECK_PARAMS reflects the check-owning node ---
# State-scoped params (BRANCH unset) and branch-scoped params (BRANCH set) are
# each forwarded verbatim. A stub check echoes CHECK_PARAMS back as its feedback.
SBX2=$(mktemp -d); mkdir -p "$SBX2/checks"
cat > "$SBX2/protocol.json" <<'JSON'
{"name":"p","states":[
  {"id":"s","params":{"categories":["a","b"]},"checks":[{"run":"echo-params"}],
   "branches":[{"id":"bx","params":{"categories":["only-b"]},"checks":[{"run":"echo-params"}]}]}
]}
JSON
cat > "$SBX2/checks/echo-params.sh" <<'SH'
#!/usr/bin/env bash
jq -nc --arg f "${CHECK_PARAMS:-MISSING}" '{check:"echo-params",pass:true,feedback:$f}'
SH
chmod +x "$SBX2/checks/echo-params.sh"

OUT=$("$RC" "$SBX2/protocol.json" s "$FX/evidence-complete.json" "$FX/diff-pr1.txt" "$FX/changed-files-pr1.txt")
chk "params: state-scoped forwarded" 'jq -e ".results[0].feedback|fromjson|.categories==[\"a\",\"b\"]" <<<"$OUT" >/dev/null'
OUT=$(BRANCH=bx "$RC" "$SBX2/protocol.json" s "$FX/evidence-complete.json" "$FX/diff-pr1.txt" "$FX/changed-files-pr1.txt")
chk "params: branch-scoped overrides state" 'jq -e ".results[0].feedback|fromjson|.categories==[\"only-b\"]" <<<"$OUT" >/dev/null'
rm -rf "$SBX2"

echo "-----"
echo "run-checks tests: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
