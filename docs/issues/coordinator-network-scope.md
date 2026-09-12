# The coordinator has unrestricted network access

## Priority

Medium — scope and isolation.

## Problem

The coordinator profile enables full network access so it can reach the local HTTP
service and Codex infrastructure. This also permits arbitrary outbound connections.
If child-controlled content influences the coordinator, unrestricted egress
increases the potential impact.

## Desired behavior

After the orchestration flow is stable, restrict the coordinator to the required
local endpoint and explicitly required OpenAI destinations using enforceable
network controls.

## Acceptance criteria

- Required destinations and protocols are documented.
- Unexpected outbound destinations are denied.
- The live E2E succeeds with the narrowed network policy.
- A negative test demonstrates that arbitrary egress fails.
