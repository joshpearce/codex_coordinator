# Command approvals ignore the request's offered decisions

## Priority

High — governance integrity.

## Status

Resolved. `ApprovalPolicy.constrain()` now treats every explicit normalized
`availableDecisions` list as a response ceiling. `approve_once` requires an offered
`accept`, and `approve_session` requires both an offered `acceptForSession` and
trusted session enablement; otherwise the verdict becomes a denial. The one-shot
handler and live broker share this constraint, with regressions for denial-only
command requests in both paths.

## Problem

Command approval normalization validates the shape of `availableDecisions`, but
the deterministic constraint and response encoder do not require an approving
response to be one of those choices. A request that explicitly offers only
`decline` and `cancel` can therefore be encoded as `accept` when a judge returns
`approve_once`.

This lets a fallible or adversarial judge authorize a command even though the
request's own decision ceiling contains no approving option.

## Desired behavior

An explicit `availableDecisions` list should be an immutable ceiling on the wire
response. If it contains no supported approving choice, an approving judge verdict
must fail closed.

## Acceptance criteria

- A command request offering only denial choices can never produce `accept` or
  `acceptForSession`.
- Every approving command response corresponds to an approving choice present in
  the normalized request.
- An unavailable approving verdict is reduced to denial before response encoding.
- Regression tests cover denial-only lists for both one-shot and live approval
  paths.
