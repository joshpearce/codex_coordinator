# Mutable permission evidence can widen request scope

## Priority

High — governance integrity.

## Status

Resolved. Nested normalized permission maps and path collections are recursively
immutable. Serialization and event logging use detached copies, so downstream
changes cannot alter the pending authoritative case. Returned permissions are
normalized and checked against both that original request and the immutable trusted
policy ceiling. Regressions cover a mutating one-shot judge, a mutated live event,
permission widening, and valid narrowing.

## Problem

The normalized permission request is wrapped only at its top level. Nested
permission mappings and path lists remain mutable while the judge runs. A mutating
judge can add a second path to a one-path request and return both paths. If both fit
the trusted policy ceiling, `constrain()` compares the verdict with the already
mutated request and approves authority that the caller never requested.

Checking the trusted ceiling is insufficient: a judge must be able to narrow the
caller's request, but must never widen it within that ceiling.

## Desired behavior

Permission constraints should compare the judge's verdict with a recursively
immutable, pre-judge snapshot of the normalized caller request as well as the
trusted policy ceiling.

## Acceptance criteria

- A judge cannot mutate nested permission mappings or path collections.
- Returned permissions must be a subset of the original normalized request and
  the trusted ceiling.
- Mutation attempts fail closed rather than becoming part of the authority check.
- A regression test proves that a mutating judge cannot widen a one-path request
  to two allowed paths.
