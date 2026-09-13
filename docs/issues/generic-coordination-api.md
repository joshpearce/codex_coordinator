# Expose a reusable coordination API

## Status

Complete — child of [the generic coordination goal](generic-coordination-goal.md).
The public Python API, typed manual approval view, concurrent-session and
lifecycle tests, policy-constrained judge behavior, and installed-wheel
two-project fixture pass. This closes the
API contract only; the parent goal's live Codex end-to-end gate remains open.

## Problem

`codex_coordinator` exports governance and supervisor classes, but a caller
must assemble transport, broker, event polling, and session bookkeeping or use
the low-level HTTP routes directly. The repository's multi-worker coordinator
is an example-specific agent workflow, not an installable application API.

## Desired behavior

Provide a documented Python-facing coordination interface over the shared
approval boundary. It should let a host create and continue worker sessions,
consume typed events, resolve approvals through a caller-supplied judge or
explicit decision callback, await terminal state, cancel work, and close cleanly.
Keep the transport and policy internals available only as lower-level building
blocks; do not fork the governance logic.

## Acceptance criteria

- A small public API defines the coordinator, session handle, event, approval,
  and terminal-result contracts, with type hints and failure semantics.
- A caller can start multiple worker sessions, correlate every event and
  approval to the correct session, send follow-ups, and await completion
  without constructing HTTP requests by hand.
- The caller supplies judge behavior; a model verdict remains advisory and
  always passes through `ApprovalPolicy` before a wire response is sent.
- Tests cover concurrent sessions, cross-session approval rejection, follow-up
  ordering, callback failure, and clean shutdown.
- A minimal example imports only installed package modules and uses projects
  unrelated to the checked-in inventory demonstration.
