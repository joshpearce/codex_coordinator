# 0024 — Worker reasoning effort is fixed for the service lifetime

**Found:** 2026-09-17, live SharpSast coordination run
**Affects:** codex_coordinator, operator configuration and session HTTP API

## What happens

`worker_reasoning_effort` is loaded once at service startup and applied to every
worker turn. `POST /sessions` and `POST /sessions/{id}/messages` accept only a
prompt, so a coordinator cannot use a low-cost orientation turn and a stronger
implementation turn without restarting the service and losing its in-memory
session.

Prompt wording cannot change the runtime setting and must not be reported as if
it did.

## Why it matters

The service cannot execute a bounded phase plan whose effort changes between
turns. Operators must either overspend on orientation or undersupply later work,
and the effective selection is not visible in session state.

## What would close this

- Trusted configuration declares the finite set of worker efforts a coordinator
  may select and a default member of that set.
- Session creation and follow-up requests may select only a declared effort.
- The selected effort is sent on the wire and recorded in session/events.
- Offline tests cover default, accepted override, and rejected override; a live
  run confirms the app-server accepts the selected values.
