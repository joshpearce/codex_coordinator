# 0016 — Every mundane project-local command spends a judge call

**Found:** 2026-09-14, live networked E2E run in workspace
`codex-orchestration-e2e-gf6xzxi3`, approval-traffic review plus offline probing
of Codex CLI 0.154.0 execpolicy
**Affects:** codex_coordinator, worker session startup and approval routing,
`src/codex_coordinator/service.py`, `src/codex_coordinator/coordinator.py`,
`examples/operator/*.permissions.toml`

## What happens

The run raised 17 approval requests across two children: 11 command executions
and 6 file changes. Sixteen were approved and one file change was denied. Nine
of the 11 command executions were project-local reads, searches, or the
project's own test and CLI entry points:

| # | Project | Command as received |
|---|---|---|
| 1 | inventory-app | `sed -n '1,240p' README.md && sed -n '1,320p' test_inventory_app.py && sed -n '1,320p' inventory_app/domain.py` |
| 2 | inventory-report | `sed -n '1,240p' README.md && sed -n '1,260p' test_inventory_report.py && sed -n '1,240p' inventory_report/report.py` |
| 3 | inventory-app | `find inventory_app -maxdepth 2 -type f -print -exec sed -n '1,260p' {} \;` |
| 4 | inventory-report | `find . -maxdepth 3 -type f -not -path './.git/*' -print -exec sed -n '1,220p' {} \;` |
| 5 | inventory-report | `rg -n "reasonably\|Total value\|unit_price\|TODO" . --hidden -g '!/.git'` |
| 9 | inventory-report | `python -m unittest -q && python -m inventory_report --help` |
| 10 | inventory-app | `python -m unittest -q && python -m inventory_app --help` |
| 11 | inventory-report | `sed -n '10,80p' inventory_report/report.py` |
| 12 | inventory-app | `sed -n '1,120p' inventory_app/domain.py` |
| 15 | inventory-report | `if rg -n 'TODO\|NotImplementedError' inventory_report/report.py; then exit 1; fi` then `python -m unittest -q` |
| 17 | inventory-app | `if rg -n 'TODO\|NotImplementedError' inventory_app/domain.py; then exit 1; fi` then `python -m unittest -q` |

Every one of them arrived as `<login shell> -lc "<script>"` — in this run
`/run/current-system/sw/bin/zsh`. The script is a single opaque argument, and
requests 1, 2, 15 and 17 chain several commands inside it with `&&`, a newline,
or an `if … then … fi` guard.

The runtime's own parse in `commandActions` covered part of that: across the 11
requests it produced 8 `read` actions, 1 `search`, and 6 `unknown`. The reads
carry absolute resolved paths; the single search action carried the
unresolved relative path `.`. The `unknown` actions are the `find -exec` pair
and the compound test-run scripts — the class most worth allowing is the class
the parse does not describe.

Nothing in this run was decided wrongly. Every verdict matched what the
constitutions say, including the denial. What the evidence shows is a routing
defect rather than a judging defect: nine requests with no decision content each
cost a separate judge model call, serialized against the child's turn, and each
one adds another untrusted-evidence surface to normalize, record, and retain.

The deterministic layer already confines these: the turn sandbox is
`workspace-write` on the project alone with no network, and `normalize_path`
rejects any path outside the registered project before a judge sees the case.
The reads above are contained twice over and judged anyway.

### What is not yet known

Codex CLI 0.154.0 has an execpolicy layer that could decide this class without a
model. Probing it offline established the following, and left the rest open.

Established by running `codex execpolicy check --rules <file> <command…>`:

- Rule files are Starlark. `prefix_rule(pattern=[…], decision=…,
  justification=…)` is real; `decision` accepts `allow`, `prompt`, and
  `forbidden`, and a fourth value fails the parse with `invalid decision`.
- A pattern element is a string or a list of strings, where a list means
  alternatives: `["python", "-m", ["unittest", "pytest"]]` matches
  `python -m unittest -q`. There is no regex in a pattern element; a dict is
  rejected with `pattern element must be a string or list of strings`.
- Matching is by argv prefix, and trailing arguments are unconstrained:
  `["git", "status"]` matches `git status --short`. A rule therefore constrains
  the program and its leading flags, **not** the paths it touches.
- The parser also exposes `match`, `not_match`, `network_rule(protocol=…)`,
  `paths`, `host_executable` and `justification`; their signatures were not
  worked out. `paths` is not a `prefix_rule` parameter.
- `codex execpolicy check` does **not** match through a shell wrapper: a rule for
  `sed -n` returns no match for `zsh -lc "sed -n '1,10p' README.md"`. Whether the
  runtime unwraps the shell before evaluating rules is unknown; `core/src/exec_policy.rs`
  in the binary references `/bin/zsh`, `-lc`, `-c` and a list of interpreters,
  which suggests some unwrapping exists somewhere in that path.

Established from the generated app-server schema and the binary:

- `thread/start` accepts a free-form `config` object of per-thread configuration
  overrides, so a policy could be delivered per session on the wire.
- The running app-server accepted `{"definitely_not_a_config_key": 1}` in that
  object without error, as it did a bogus key nested under `exec_policy`.
  **A misnamed policy key fails open and silently**, with no rules loaded and no
  diagnostic.
- `core/src/exec_policy.rs` logs `rules`, `policy_paths` and `default.rules`,
  which points at an `exec_policy` configuration table, but the accepted
  spelling was not confirmed and cannot be confirmed by any offline check.
- `AskForApproval` also accepts `{"granular": {rules, sandbox_approval,
  mcp_elicitations, request_permissions, skill_approval}}`, which is how a
  caller says "escalate when a rule says prompt". `WorkerPermissions` currently
  accepts only the `on-request` and `untrusted` strings.
- The approval protocol lets a reviewer return `acceptWithExecpolicyAmendment`,
  extending the policy so future matching commands stop prompting. The request
  side of that, `proposedExecpolicyAmendment`, is authored inside the worker's
  session. `ApprovalPolicy` already normalizes it and no judge may grant it.

## Why it matters

Governance quality and operator effort. A reviewer reading 17 requests where
nine carry no decision is worse at catching the one that does, and the judging
budget for a run is spent mostly on traffic that code could resolve. Allowing
this class deterministically also makes what remains meaningful: the file
changes, and anything whose shape was not anticipated.

It is also the first trusted input that would be consumed by the Codex runtime
rather than by this codebase, and the probe above shows that path fails open. A
policy that silently does not load produces exactly the behavior seen today —
which would read as "the feature works, approvals are just noisy" — while a
policy that loads but is too broad silently stops escalating things that should
be judged. Both failure directions are quiet, so the delivery mechanism has to
be chosen for verifiability, not only for convenience.

## What would close this

- A selected rule set exists, encoded as rules rather than prose, covering the
  reads (`sed -n` ranges), the searches (`rg`), and the project's own test and
  CLI entry points (`python -m unittest`, `python -m <package> --help`).
  `find … -exec` is deliberately excluded: it runs an arbitrary program per
  match and only looks mundane. File changes are out of scope entirely — they
  are a different approval method and must keep reaching a judge.
- The allow list lives in an execpolicy, never in a constitution. A constitution
  that named these commands would break the principle the two tiers exist to
  protect, and `tests/test_judge_live_gate.py` must keep scanning constitutions
  only, with a test showing the new file is not one.
- A delivery mechanism is chosen and the rejected ones are recorded with their
  reasons, judged against three criteria: readable by an operator, maintainable
  as projects change, and not writable or influenceable from inside a worker
  session. The candidates:
  - **A.** rules inline in the `thread/start` `config` override. Nothing on disk
    near the worker; but unverifiable, since an unknown key is accepted silently.
  - **B.** an `exec_policy` block inside the project's existing
    `*.permissions.toml`. One operator-owned file per project, already validated
    for ownership and location; rules embedded in TOML read poorly.
  - **C.** a separate `*.policy` file beside `operator.toml`, referenced from the
    permissions file. Native syntax, checkable with `codex execpolicy check` in
    CI; one more file per project.
  - **D.** no Codex-side policy at all: a coordinator-side deterministic allow
    evaluated before the judge, over `commandActions` plus the containment rule
    `normalize_path` already enforces. Fully under test here and fails closed,
    but cannot decide the six `unknown` parses, which is where most of the value
    is. C and D are not exclusive.
- Before any of that is trusted: whether the runtime evaluates rules through the
  `<shell> -lc "<script>"` wrapper is settled by live evidence, since every real
  request in this run has that shape, and whether a compound script is evaluated
  as a whole or per command is settled with it.
- Loading is verifiable and fails closed. A policy the runtime did not accept
  must be detectable — at minimum a live startup probe whose expected decision is
  asserted — rather than inferred from approval volume.
- `proposedExecpolicyAmendment` stays refused. A worker must not be able to widen
  the policy that governs it by proposing an amendment a judge could approve, and
  a regression covers that the answer is still no under the new configuration.
- Regression tests cover the selected rules against the exact command strings
  recorded above, including the `find -exec` pair and an out-of-project read as
  negative cases.
- A live run shows the approval count for the same scenario falling from 17 to
  roughly 8, with every file change still judged, the denial still reached, and a
  planted out-of-project read still escalating.
