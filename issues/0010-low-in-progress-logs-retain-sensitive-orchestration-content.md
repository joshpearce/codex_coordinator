# 0010 — Logs retain sensitive orchestration content

**Found:** 2026-09-12, local security review of the JSONL event record
**Affects:** codex_coordinator, event logging, `src/codex_coordinator/service.py`

## What happens

The JSONL record includes prompts, commands, model messages, approval requests,
outputs, and diffs. This can preserve source code, local paths, credentials
printed by tools, and unrelated app-server notifications.

Partially addressed. Default stdout is metadata-only, known secret-bearing
fields and environment values are redacted, and the source-only live harness
creates its coordinator JSONL file privately without following an existing
symlink. In-memory retention is bounded.

Remaining: redirected stdout and on-disk log retention are still undocumented
operator responsibilities, and file permission and retention behavior is
untested.

## Why it matters

Data handling. The audit trail is worth keeping, but it currently has no stated
lifetime or readership, so it accumulates the most sensitive content the system
touches.

## What would close this

- Unmanaged-thread events are excluded.
- Known secret fields and environment values are redacted.
- File permissions and retention behavior are documented and tested.
- Verbose payload capture is opt-in where practical.
