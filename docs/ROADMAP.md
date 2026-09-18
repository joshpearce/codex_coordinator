# Delivery roadmap

The product direction is transparent local session orchestration. Earlier governance milestones were superseded by #0026; their history remains in Git and is not evidence that their individual controls shipped.

| Milestone | Deliverable | Completion evidence |
|---|---|---|
| M0 — Transparent orchestration (#0026, complete 2026-09-18) | Named projects, host app-server connection, inherited Codex configuration, automatic approvals, Python and HTTP APIs | 33 offline tests and the installed-wheel gate pass; the [live compatibility run](live-compatibility.md) proved host and distinct project configuration inheritance, automatic approval, and an unrestricted child that was not narrowed |
| M1 — Resilience and data handling | Bounded runtime state (#0011), event redaction/retention (#0010), HTTP resource limits (#0012) | High-volume and parser-limit tests plus documented retention behavior |
| M2 — Control-plane hardening | Listener protection (#0008) and authentication (#0009) | Required before remote, multi-user, untrusted-client, or production deployment |

M0 is complete. M1 work that already applies remains in the implementation and tests. M2 is deliberately deferred only for the documented trusted, single-user, loopback-only prototype.
