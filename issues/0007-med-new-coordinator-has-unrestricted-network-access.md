# 0007 — The coordinator has unrestricted network access

**Found:** 2026-09-12, local security review of the coordinator sandbox profile
**Affects:** codex_coordinator, coordinator profile, `examples/operator/`

## What happens

The coordinator profile enables full network access so it can reach the local
HTTP service and Codex infrastructure. This also permits arbitrary outbound
connections. If child-controlled content influences the coordinator,
unrestricted egress increases the potential impact.

## Why it matters

Scope and isolation. The coordinator reads worker output and judge reasons, both
of which can carry attacker-authored text (see #0002); unrestricted egress turns
influence over the coordinator into an exfiltration channel.

## What would close this

- Required destinations and protocols are documented.
- Unexpected outbound destinations are denied.
- The live E2E succeeds with the narrowed network policy.
- A negative test demonstrates that arbitrary egress fails.

Sequence this after the orchestration flow is stable.
