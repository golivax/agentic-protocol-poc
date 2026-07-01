# Distribution acceptance runbook

Target: https://github.com/golivax/throw-away-repo

## 0. Prereqs
- `gh auth status` shows repo,workflow scopes; `gh extension list` shows github/gh-aw.
- You hold a PAT for POC_DISPATCH_TOKEN (repo+workflow) and your Claude Code auth token.

## 1. Install both protocols
```bash
git clone https://github.com/golivax/throw-away-repo && cd throw-away-repo
bash <(curl -fsSL https://raw.githubusercontent.com/golivax/agentic-protocol-poc/main/dist/install.sh) \
  install code-review recover-mental-model-stub --base-url <your-funnel-url>
```
- Pick `claude` for each agent prompt.
- At the endpoint step: review the previewed engine.env, confirm.
- Enter POC_DISPATCH_TOKEN when prompted; enter your Claude Code token for ANTHROPIC_API_KEY in the gh-aw secret prompt.

## 2. Verify the unit landed
- `git log -1` on the default branch shows the install commit.
- `.github/agent-factory/{engine,protocols/code-review,protocols/recover-mental-model-stub}` present.
- `.github/agent-factory/.install.json` lists both protocols + engine_version.
- `git branch -r | grep agentic-state` exists.
- `.github/workflows/{preflight,grumpy,security,rmm-*}-agent.lock.yml` were compiled (present locally / committed).

## 3. Run code-review
- Open a PR; comment `/review`.
- Expect: pipeline check-run + status comment; preflight → review fanout → join → approval gate.

## 4. Run recover-mental-model-stub
- Comment `/recover` on an issue/PR per the protocol's trigger.
- Expect: fanout/sub-pipeline runs; the `/answer` gate opens; answer it; merge/combine completes.

## 5. Update smoke
- Bump a protocol `version` in the source (or use a newer `--ref`), then:
  `bash dist/install.sh update`
- Expect: only changed files rewritten, orphans removed, drift warning if you locally edited a file, `agentic-state` untouched.
