# 0006 — One-shot judges may read overly broad local data

**Found:** 2026-09-12, local security review of the one-shot judge sandbox
**Affects:** codex_coordinator, one-shot judge invocation,
`src/codex_coordinator/coordinator.py`

## What happens

A read-only sandbox prevents mutation but may still expose files outside the
child project. A judge could inspect unrelated repository or user data while
evaluating an approval. Structured output constrains the response format, not
what the judge can read or incorporate into its reasoning.

The one-shot route is now hardened. Following the
[official permissions-profile documentation](https://learn.chatgpt.com/docs/permissions),
the judge requests a deny-by-default filesystem profile without the legacy
`--sandbox` override, disables auxiliary tools, and probes an allowed read plus
an unrelated denied read before each invocation. Probe failure stops the judge.
A runtime denied-read regression test exists. The pinned CLI exposes
`codex sandbox --permission-profile`; the regression test passed on an
unrestricted operator host and in hosted macOS CI, and a live `codex exec` judge
decision returned a valid denial under this profile after a successful
allowed-read, denied-read, and executable preflight. The same test cannot start
inside the restricted development workspace
(`sandbox-exec: sandbox_apply: Operation not permitted`).

The separate App Server path is not covered.
[Official App Server documentation](https://learn.chatgpt.com/docs/app-server)
describes `sandboxPolicy.readOnly.access` with a restricted `readableRoots`
list, but the generated `TurnStartParams` schema from the pinned `codex-cli
0.154.0` does **not** include that field. Do not treat an isolated working
directory or a read-only write restriction as a substitute.

## Why it matters

Scope and isolation. A judge with broad reads can pull secrets and unrelated
project data into reasoning that is then summarized into an event stream the
coordinator reads.

## What would close this

- The judge runs with an explicit minimal read allowlist.
- Secrets, sibling projects, and unrelated user files are unavailable.
- The verdict record identifies the evidence provided to the judge.
- A test proves an attempted unrelated read is denied.
- A Codex version upgrade that exposes the App Server restricted-read field
  passes the generated-schema compatibility gate and a real denied-read test.
