# Logs retain sensitive orchestration content

## Status

Partially addressed. Default stdout is metadata-only, known secret-bearing
fields and environment values are redacted, and the source-only live harness
creates its coordinator JSONL file privately without following an existing
symlink. In-memory retention is bounded; redirected stdout and on-disk log
retention remain operator responsibilities. This issue is not closed.

## Priority

Low — data handling.

## Problem

The JSONL record includes prompts, commands, model messages, approval requests,
outputs, and diffs. This can preserve source code, local paths, credentials printed
by tools, and unrelated app-server notifications. Log files currently have no
redaction, retention, or access policy.

## Desired behavior

Keep the useful audit trail while minimizing sensitive content and limiting its
lifetime and readership.

## Acceptance criteria

- Unmanaged-thread events are excluded.
- Known secret fields and environment values are redacted.
- File permissions and retention behavior are documented and tested.
- Verbose payload capture is opt-in where practical.
