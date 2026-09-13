# Enforce the supported security boundary

## Status

Open — child of [the generic coordination goal](generic-coordination-goal.md).
Local loopback, project-root, socket, origin/Host, and log-default controls
are implemented. Item evidence is now bound to thread, turn, and item identity,
so a repeated item ID cannot reuse a prior turn's command or file evidence.
File approvals now require the matching `item/started` change list; an approval
request cannot supply or replace it.
The one-shot judge now requests a deny-by-default permissions
profile and fails closed if a pre-judge read-isolation probe does not pass.
Its runtime denied-read regression test passed on the operator host as part
of the 172-test full suite. A live one-shot judge decision using that same
profile has not yet been verified, so this issue remains open. The pinned
Codex CLI 0.154.0 generated schema also
does not expose the restricted-read turn policy described in current OpenAI App
Server documentation; see [judge read scope](judge-read-scope.md).

## Problem

The package advertises a single-user local prototype, but the service accepts
arbitrary project paths, exposes an unauthenticated control API, permits
non-loopback binding through a flag, and records sensitive orchestration data.
One-shot judges may read unrelated local files. These limits are significant
when the same artifact is reused by other coordination workflows.

## Desired behavior

Make the supported local, single-user boundary enforceable and testable. Do not
equate generic reuse with production or remote safety. Any remote or multi-user
mode must be a separate, explicitly secured deployment path rather than an
accidental consequence of changing `--host`.

## Acceptance criteria

- The default service is loopback-only; unsupported non-loopback binding is
  rejected unless a separately documented authenticated deployment mode exists.
- Project-root authorization and socket ownership/type/mode checks occur
  before session creation or connection. Security errors are fail-closed.
- Judge inputs and filesystem reads are restricted to the evidence needed for
  the decision; a test proves an unrelated file is unavailable.
- Event/log output has documented permissions and retention, redacts known
  secret-bearing fields, and avoids verbose payload capture by default where
  possible.
- Security documentation gives a concrete threat model and states whether
  local processes, browser-origin requests, remote clients, and multiple users
  are trusted or excluded. If any of those are supported, authentication and
  transport protection are tested before this issue closes.

The detailed open security issues remain authoritative: [arbitrary project
paths](arbitrary-project-paths.md), [judge read scope](judge-read-scope.md),
[coordinator network scope](coordinator-network-scope.md), [sensitive event
logs](sensitive-event-logs.md), [control-plane authentication](control-plane-authentication.md),
and [socket/listener protection](socket-listener-protection.md). A local-only
release may defer remote-mode work only if the unsafe mode cannot be enabled.
