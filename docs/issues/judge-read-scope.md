# One-shot judges may read overly broad local data

## Priority

Medium — scope and isolation.

## Problem

A read-only sandbox prevents mutation but may still expose files outside the child
project. A judge could inspect unrelated repository or user data while evaluating
an approval. Structured output constrains the response format, not what the judge
can read or incorporate into its reasoning.

## Desired behavior

Judges should receive a minimal evidence bundle and have filesystem reads restricted
to only the constitution, normalized request, and explicitly necessary project
metadata.

## Current compatibility finding

[Official OpenAI App Server documentation](https://learn.chatgpt.com/docs/app-server)
describes `sandboxPolicy.readOnly.access` with a restricted `readableRoots`
list. The generated `TurnStartParams` schema from this project's currently
pinned `codex-cli 0.154.0` does **not** include that field. Do not treat an
isolated working directory or a read-only write restriction as a substitute.
A Codex version upgrade using that App Server field must pass the generated-
schema compatibility gate and a real denied-read test before this issue can
close.

[Official permissions-profile documentation](https://learn.chatgpt.com/docs/permissions)
also describes deny-by-default filesystem profiles. The one-shot judge now
requests such a profile without the legacy `--sandbox` override, disables
auxiliary tools, and probes an allowed read plus an unrelated denied read
before each judge invocation. Probe failure stops the judge. There is also a
runtime denied-read regression test. The pinned CLI
exposes `codex sandbox --permission-profile`; the regression test passed on an
unrestricted operator host as part of the 172-test full suite. The same test
cannot start inside the restricted development workspace
(`sandbox-exec: sandbox_apply: Operation not permitted`). Confirmation that
the CLI honors the profile during an actual `codex exec` judge turn and a live
judge decision are still required. Do not relax the judge boundary based on
CLI flags or mocked subprocess tests alone.

## Acceptance criteria

- The judge runs with an explicit minimal read allowlist.
- Secrets, sibling projects, and unrelated user files are unavailable.
- The verdict record identifies the evidence provided to the judge.
- A test proves an attempted unrelated read is denied.
