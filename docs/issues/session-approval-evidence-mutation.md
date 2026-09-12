# Mutable command evidence can enable session-wide approval

## Priority

High — governance integrity.

## Status

Resolved. Normalization now recursively freezes the entire `ApprovalCase`, including
nested decision lists, before a judge or live consumer sees it. One-shot mutation
attempts raise and are caught by the handler's fail-closed boundary. Live events
receive detached mutable copies, while resolution uses the separately retained
immutable case. Session eligibility is therefore checked only against the original
normalized request. Regression tests cover mutation attempts and valid session
approval.

## Problem

`ApprovalCase.request` is protected only by a shallow `MappingProxyType`. Nested
objects remain mutable, including the command request's `availableDecisions` list.
An in-process judge can append `acceptForSession` to that list and return
`approve_session`. When trusted policy permits session approval, the deterministic
constraint reads the judge-mutated evidence and emits `acceptForSession`, even
though the caller's original request still contains no session-wide option.

The authority check therefore occurs against evidence the untrusted judge can
change after normalization rather than against the request that crossed the
governance boundary.

## Desired behavior

The judge should receive a recursively immutable view of the normalized request,
and authorization should always be checked against a sealed snapshot captured
before judge execution.

## Acceptance criteria

- A judge cannot mutate nested command evidence, including
  `availableDecisions`.
- Session approval is evaluated against the pre-judge normalized request.
- Attempted evidence mutation fails closed and cannot change the encoded response.
- A regression test uses a mutating judge and proves that a request without
  `acceptForSession` cannot obtain session-wide approval.
