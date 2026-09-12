# The live approval broker does not enforce the constitution

## Priority

High — governance integrity.

## Problem

`ApprovalBroker.resolve()` trusts the posted verdict. It permits session-wide
approval when requested and returns the request's permission object without a
deterministic allowlist, path ceiling, or constitution check. The E2E coordinator
asks one-shot Codex judges for decisions, but that convention is not enforced by
the service. A project session can shape a request that receives authority beyond
the intended policy if the coordinator or judge is mistaken.

## Desired behavior

The long-running service should apply the same fail-closed policy boundary as the
one-shot `JudgedApprovalHandler` before encoding any approval response.

## Acceptance criteria

- Unknown approval methods are denied.
- Requested paths and permission keys are checked against the registered project.
- Session-wide approvals are disabled unless explicitly enabled by trusted policy.
- A judge can narrow or deny authority but cannot exceed the deterministic ceiling.
- Tests exercise forged, malformed, and over-broad requests.
