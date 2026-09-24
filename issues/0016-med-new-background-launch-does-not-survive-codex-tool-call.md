# 0016 — The documented background launch does not survive a Codex tool call

**Found:** 2026-09-24, review of parent Codex session `01a0d0d4-63b7-7222-98bb-236e2beff44b`
**Affects:** codex_coordinator, `examples/coordination-workspace/Makefile`, coordination-workspace documentation

## What happens

The supplied coordination-workspace `make start` recipe uses `nohup` and a
background process, reports success after one second, and returns control to the
parent. In the reviewed Codex execution environment that process was reaped as
soon as the tool call completed. The PID file and log briefly indicated a
successful start, but the HTTP listener was gone on the next request.

The parent worked around this by running `codex-coordinator-service` in a
foreground PTY execution cell and manually keeping that cell alive. This is not
the advertised quickstart workflow and contributed to repeated restarts.

## Why it matters

Access and reproducibility. The primary documented use is a Codex parent in a
coordination workspace, but the supplied launcher was not durable in that exact
environment. A false successful start also sends the parent into avoidable
diagnostic and recovery work.

## What would close this

- The supported coordination-workspace startup method remains reachable after
  the initiating Codex tool call returns, or the documented workflow explicitly
  requires and verifies an externally supervised service.
- Startup success is based on a durable post-launch health check rather than
  only process existence after one second.
- Status detects stale PID/port state and explains the supported recovery.
- A live Codex-parent exercise proves start, multiple later HTTP calls, status,
  and orderly shutdown without a foreground tool cell.
