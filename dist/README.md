# Distributing agentic protocols

Install a protocol (the engine + a protocol + its agent workflows) into any repo.

## Prerequisites
- `gh` ≥ 2.0 authenticated with `repo,workflow` scopes (`gh auth status`)
- The gh-aw extension: `gh extension install github/gh-aw`
- GitHub Actions enabled on the target repo; write access

## Install
```bash
git clone https://github.com/<you>/<target> && cd <target>
curl -fsSL https://raw.githubusercontent.com/golivax/agentic-protocol-poc/main/dist/install.sh \
  | bash -s -- install code-review
```
Install several at once: `... install code-review recover-mental-model`.
List what's available: `... list`. Update later: `... update`.

During install you pick an engine per agent workflow (via the gh-aw wizard) and,
optionally, configure a custom endpoint — the installer shows exactly what it will
write before doing so.
