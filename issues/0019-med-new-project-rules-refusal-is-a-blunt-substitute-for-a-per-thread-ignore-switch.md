# 0019 — Refusing every project that carries Codex rules is a blunt substitute for a per-thread ignore switch

**Found:** 2026-09-14, on closing the worker-written project rules finding; the
refusal shipped as the only available remedy, not as the preferred one
**Affects:** codex_coordinator, session startup,
`src/codex_coordinator/coordinator.py` (`find_worker_project_rules`),
`src/codex_coordinator/service.py` (`CoordinatorService._refuse_project_rules`)

## What happens

The Codex runtime loads execpolicy rules from `<cwd>/.codex/rules` at thread
start. Re-probed against an isolated `codex app-server --stdio` on 2026-09-14:
a thread started in a project holding `.codex/rules/worker.rules` logged
`codex_core::exec_policy: loaded 1 .rules files in <project>/.codex/rules`, and
under `approvalPolicy: untrusted` with `sandbox: workspace-write` an `allow`
rule there suppressed the approval request entirely — the worker's
`cat NOTES.md` ran with no approval at all. `--strict-config` rejects
`exec_policy`, `execpolicy`, and `rules` as unknown app-server fields, and
`--ignore-rules` exists for `codex exec` but not for the app-server.

Because the runtime cannot be told to ignore those rules, the coordinator
refuses the project instead: a `.codex/rules` entry, any `*.rules` file under a
`.codex` directory, or a symlinked `.codex` the scan cannot see through denies
the session.

That is blunt in both directions. It denies a session to a project that may
carry rules for legitimate reasons — a checkout shared with a developer's own
Codex usage, a vendored tree, a repository that ships rules for other tools —
and the coordinator cannot distinguish those from a widening one without
reading a file it has deliberately decided not to trust. It also extends to a
symlinked `.codex`, where the finding is the scan's own blind spot rather than
any observed rule.

## Why it matters

Scope and operator effort. The refusal is correct for governance and is the
only remedy the pinned CLI allows, but it makes a class of ordinary project
unusable with this coordinator, and the reason is upstream rather than
intrinsic. Recorded so that the refusal is understood as provisional and is
removed when it stops being necessary, rather than hardening into a permanent
restriction nobody revisits.

## What would close this

- A pinned CLI offers an app-server or per-thread switch that ignores project
  rules — a `thread/start` parameter, or a configuration key `--strict-config`
  accepts. The coordinator sets it and asserts it took effect using the
  loaded-vs-ignored signal from #0016's record: with the switch set and a rules
  file planted, the app-server trace must report zero `.rules` files loaded
  from the project directory, and a command the planted rule would have allowed
  must still raise an approval.
- Only after that live proof is the refusal replaced by the switch. Until then
  it stays; a configuration key that is accepted but unverified is not evidence.
- If a later CLI instead removes project-directory rule loading altogether, the
  probe above shows zero loaded files with no switch set, and the refusal and
  its scan are deleted.
- `docs/security.md` records whichever outcome held, with the probe date and
  the CLI version it was observed on.
