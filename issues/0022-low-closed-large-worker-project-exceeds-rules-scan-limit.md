# 0022 — Large worker project exceeds the rules scan limit

**Found:** 2026-09-17, local operator setup, by running preflight against `/Users/josh/code/sharp_sast`
**Affects:** Codex Coordinator, worker-project rules refusal, `src/codex_coordinator/coordinator.py`

## What happens

The mandatory worker-project rules scan refuses `sharp_sast` before startup
because the repository contains about 348,000 filesystem entries and the scan
is capped at 200,000. The scan fails closed as designed, but this legitimate
worker project cannot be coordinated.

## Why it matters

Large source trees cannot use the service even when the complete scan would
find no worker-owned Codex rules. The only operator workarounds are to remove
project content or weaken the refusal boundary.

## What would close this

Retain a finite full-tree scan and its fail-closed behavior, raise the bound
enough for the observed repository, cover the bound in an offline regression
test, pass the full test suite, and run installed preflight successfully against
the real `sharp_sast` tree.

## Resolution

On 2026-09-17, `RULES_SCAN_LIMIT` in
`src/codex_coordinator/coordinator.py` was raised to 1,000,000 while preserving
the complete walk and fail-closed exception. `tests/test_service.py` records the
large-generated-tree regression floor, and `docs/architecture.md` records the
finite operational ceiling.

The focused service suite passed 87 tests and the full suite passed 388 tests.
The package was then reinstalled from this checkout. Installed preflight
completed against `/Users/josh/code/sharp_sast`, reported Codex CLI 0.154.0,
the intended `sharp_sast_worker` profile, and the 16-rule operator exec policy;
the launched loopback service returned `{"ok": true}` from `/health` before a
clean shutdown removed its private socket.
