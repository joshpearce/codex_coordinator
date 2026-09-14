# 0001 — The network permission ceiling is one bit, so any network worker can reach any host

**Found:** 2026-09-13, multi-capability workspace assessment, code review of the
three-worker (mail/self-modifying/web) configuration
**Affects:** codex_coordinator, approval policy, `src/codex_coordinator/coordinator.py`

## What happens

`normalize_permissions` models network as `{"enabled": bool}` and
`permissions_within` compares only that flag (`coordinator.py:365-371`,
`coordinator.py:385-392`). There is no host, protocol, or direction dimension.

A worker that ingests third-party text and needs the network cannot function
under the enforced sandbox, which sets `networkAccess: False`
(`coordinator.py:85-96`). The operator must therefore grant
`permission_ceilings[PROJECT] = {network = {enabled = true}}` in `operator.toml`.
That single grant is indistinguishable from a grant to reach any host. Ingested
text that says "fetch this URL with the thread contents in the query string"
produces a permission request the judge has already been told is within policy,
and a command request whose `cwd` and write scope are perfectly in-project.

The grant is also turn-scoped rather than request-scoped: `_encode` returns
`scope: "turn"` (`coordinator.py:588-598`), so no further approval is required
for anything the worker does with the network for the rest of that turn. The
judge sees one request; an attacker gets a whole turn.

This is an analysis of the current code, not a live experiment. Constitution
rule 3 ("does not use the network") is what currently prevents it, and that is
prose evaluated by a model on evidence the attacker wrote.

## Why it matters

Governance integrity. The deterministic ceiling is the control that is supposed
to hold when the judge is fooled. For network permissions it grants everything
or nothing, so the damaging action is inside the ceiling and only the natural
language constitution stands against exfiltration.

## What would close this

- The permission ceiling expresses network grants as an explicit host or host
  pattern allowlist, and `permissions_within` rejects a requested host that is
  not in the ceiling.
- A regression test registers a project with a single-host ceiling and shows a
  request for a second host denied deterministically, with no judge call.
- The effective grant scope for a network permission is documented, and either
  narrowed below the turn or explicitly accepted in `docs/security.md`.
- Confirming the exfiltration path requires the opt-in live E2E with a real
  ingestion source and real injected text; the fast offline suite is not
  sufficient evidence.
