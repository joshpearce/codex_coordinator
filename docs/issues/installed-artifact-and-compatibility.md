# Verify the installed artifact and Codex compatibility

## Status

Open — child of [the generic coordination goal](generic-coordination-goal.md).
A wheel and sdist built in isolation, fresh off-checkout installation, all
entry points, installed two-project fixture, and Codex 0.154.0 generated-schema
gate passed locally using cached wheels. The strict live release gate now
passes on an unrestricted operator host: two unrelated workers produced the
correlated accepted and declined approvals, both terminal turns, and a
completed follow-up. A hosted CI run remains outstanding. The owner chose to
keep the repository unlicensed for now, so
package metadata intentionally omits a license; this is a documented owner
decision, not an accidental packaging omission.
The installed fixture is packaged as `codex_coordinator.installed_smoke`, so
the gate invokes it off-checkout without importing a source-tree test script.
It accepts two existing operator-selected project paths and leaves their
configuration unchanged; the full gate supplies those paths before attempting
the live example. Its release checks raise explicit failures rather than relying
on Python assertions that `-O` could remove.
The `Verify` workflow is configured to run the full suite and installed gate
in CI, but a hosted run of that workflow has not yet been observed. The full
local suite passed on the operator host with 172 tests, including the real
judge denied-read, execution-boundary, and loopback-listener tests.
Preflight now probes an existing socket listener, and both the service and
Python API provide actionable connection errors. Preflight also reports a
Codex executable disappearing between checks or a failed sign-in probe as an
actionable error instead of an assertion or raw subprocess exception.
A real Codex app-server `initialize` handshake now passes with disposable
state under `CODEX_HOME`, following the
[official state-location guidance](https://learn.chatgpt.com/docs/config-file/config-advanced).
The subsequent sandboxed command fails in this restricted development
workspace before the test command starts, but passes on the operator host.
An unsupported `historyMode: "paginated"` request was removed from both
worker startup paths after checking the
[official App Server contract](https://learn.chatgpt.com/docs/app-server);
the installed fixture now rejects its reintroduction. A live Codex run
verified the corrected wire behavior.
The generated-schema gate now also checks `item/started` evidence and the
thread/turn start response IDs used for session correlation. Missing or
malformed definitions produce a compatibility error rather than a raw lookup
exception; the pinned CLI 0.154.0 still passes this strengthened gate.
The approval schema permits a null command in the request but requires the
command and working directory on a command item. The policy now uses those
fields only from a matching same-turn `item/started` event, and denies missing
or conflicting evidence; the gate checks both sides of that contract.
Likewise, the current file-approval request has no `changes` field; the policy
requires the matching file item's change list, and the schema gate checks that
it contains changes with paths.

## Problem

The project builds a wheel and source distribution, but tests mainly import
from the checkout and the documented install path is `make install`. The
package depends on an experimental Codex app-server schema and documents
validation against one CLI version. A green source-tree suite does not prove
that a fresh installation can run a generic workflow against a supported Codex
runtime.

## Desired behavior

Treat the built wheel as the release artifact and verify its entry points,
public API, bundled resources, and protocol contract in an isolated
environment. State the supported Codex version range and fail clearly when an
incompatible runtime or approval schema is encountered.

## Acceptance criteria

- CI builds wheel and sdist, installs the wheel in a clean supported Python
  environment outside the checkout, and imports the public API and runs both
  console entry points without development dependencies.
- The installed-artifact test runs the generic multi-worker example or an
  equivalent fixture using only installed resources and operator-provided
  project paths.
- A compatibility gate checks generated app-server schemas or targeted
  protocol fixtures for each supported Codex CLI version, including approval
  request/response shapes and session lifecycle events.
- Startup reports an actionable error for unsupported or missing Codex
  executables, incompatible schemas, or unavailable connections.
- Package metadata includes project/source URLs and supported Python versions,
  with a documented wheel/Git installation and upgrade path. License metadata
  follows the owner's explicit choice: no license is declared while the
  repository remains unlicensed. PyPI publication is optional, not a
  prerequisite for this goal.
