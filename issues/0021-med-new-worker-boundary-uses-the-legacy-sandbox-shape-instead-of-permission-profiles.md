# 0021 — The worker boundary is sent in the legacy sandbox shape, and every live test inherits the developer's Codex home

**Found:** 2026-09-15, tracing #0001's host-scoped ceiling to the wire that would
carry it, then probing the pinned CLI's `thread/start` with and without a
permissions profile
**Affects:** codex_coordinator, worker boundary and live test isolation,
`src/codex_coordinator/coordinator.py` (`WorkerPermissions`),
`src/codex_coordinator/service.py`, `src/codex_coordinator/daemon.py`,
`src/codex_coordinator/live_e2e.py`, `src/codex_coordinator/compatibility.py`,
`examples/operator/`

## What happens

Two findings from the same probe session. They are filed together because the
second is the precondition for testing the first.

### The boundary is sent in a shape the runtime calls legacy

`WorkerPermissions.enforced_sandbox` (`coordinator.py:451`) builds a sandbox
literal, `service.py:608` sends `sandbox` on `thread/start`, and `service.py:640`
and `service.py:678` send `sandboxPolicy` on the first turn and on every
follow-up. `JudgedSessionSupervisor` does the same at `coordinator.py:1276` and
`coordinator.py:1292`.

Codex 0.154.0 models permissions as named profiles. The sandbox literal is a
derived compatibility view, and the generated schema says so in the
`ThreadStartResponse`:

```
sandbox: Legacy sandbox policy retained for compatibility. Experimental clients
         should prefer `activePermissionProfile` for profile provenance.
```

Probed against a real app-server (`thread/start`, ephemeral, scratch cwd):

| sent | `activePermissionProfile` | `sandbox` |
| --- | --- | --- |
| nothing | `{"id": ":read-only"}` | `{"type":"readOnly","networkAccess":false}` |
| `sandbox: "workspace-write"` | `null` | `{"type":"workspaceWrite",...}` |
| `permissions: ":workspace"` | `{"id": ":workspace"}` | `{"type":"workspaceWrite",...}` |
| both | error `-32600` | `permissions` cannot be combined with `sandbox` |

So the unconfigured state is not an absence: every thread resolves to a profile,
chosen by `default_permissions` or by the implicit built-in. Sending `sandbox`
does not select a profile — it detaches the thread from the profile system, and
`activePermissionProfile` comes back `null`. Every thread this coordinator starts
today has no profile provenance.

The consequences are ordinary today and blocking for #0001:

- A profile is the only shape that can express a host-scoped network ceiling.
  `permissions.<id>.network` carries `mode = "limited"` and a
  `domains = { host = "allow" }` map; the wire grant type
  (`AdditionalNetworkPermissions`) remains one bit and cannot.
- Turn-level fields are documented as "Override … **for this turn and subsequent
  turns**", and only `threadId` and `input` are required. The literal re-sent at
  `service.py:678` is a reassertion of thread state that had not lapsed, not a
  per-turn re-imposition.
- The legacy readback is lossy in exactly the direction that matters. Under a
  profile limited to one host, the response's `sandbox` view reports
  `networkAccess: true`. An audit that reads the sandbox field back cannot see a
  host-scoped ceiling.
- The implicit default is not stable. With no `default_permissions`, an
  unconfigured thread resolved to `:read-only` before a trust record existed for
  its cwd and to `:workspace` afterward. Setting `default_permissions` explicitly
  pinned it across runs.

### Live tests run against the developer's own Codex home

`default_daemon_socket` (`daemon.py:10`) hardcodes
`Path.home()/".codex/app-server-control/app-server-control.sock"`, and
`ensure_daemon` (`daemon.py:60`) starts the shared daemon with no configuration
of its own. `preflight.py:63` compares against the same hardcoded path. So the
live E2E drives whatever app-server the developer's desktop and phone sessions
drive, reading `~/.codex/config.toml` as it happens to be on that machine.

`_coordinator_permission_overrides` (`live_e2e.py:407`) works around this by
passing the coordinating session's whole profile as six `--config` flags, with a
comment recording that a profile left in the working directory is silently
ignored. That is the only reason the flags exist.

`CODEX_HOME` removes the need for them. Probed, with no `-c` flags at all:

```
CODEX_HOME=<tmp>  thread/start {"permissions": "e2e_worker"}
  -> activePermissionProfile: {"id": "e2e_worker", "extends": ":workspace"}
same call without CODEX_HOME
  -> -32600 "failed to load configuration: default_permissions requires a `[permissions]` table"
```

`tests/test_runtime_boundary.py:36` already isolates this way. Four properties of
that isolation were probed and must be designed around:

1. Auth lives in `CODEX_HOME`. A fresh one reports `Not logged in`; a symlink to
   `~/.codex/auth.json` restores `Logged in using ChatGPT`.
2. The runtime writes into `CODEX_HOME`. After the probes, the supplied
   `config.toml` had gained a `[projects."<cwd>"] trust_level = "trusted"` entry,
   and the directory held `goals_1.sqlite`, `logs_2.sqlite`, `memories_1.sqlite`,
   `queue_1.sqlite`, `state_5.sqlite`, `skills/` and `tmp/`. A home pointed at a
   checked-in `examples/` directory would be mutated by the run.
3. A `[permissions]` table without `default_permissions` is a hard config error,
   so adopting profiles anywhere forces that key to be set.
4. `codex app-server --listen unix://PATH` runs a private listener, which is the
   alternative to teaching `default_daemon_socket` about `CODEX_HOME`.

### The network axes the live tests need are separately controllable

A profile separates three things the sandbox literal cannot: external hosts
(`domains`), unix sockets by path (`unix_sockets`), and loopback TCP
(`allow_local_binding`). Probed with `codex sandbox -P <profile>` against a
listener on `127.0.0.1:<port>` and a unix socket, from a process attempting a
raw connect on each:

| feature | mode | knobs | loopback TCP | unix socket | external |
| --- | --- | --- | --- | --- | --- |
| `network_proxy` on | `limited` | `domains={example.com=allow}` | blocked `EPERM` | blocked `EPERM` | `example.com` only; others `CONNECT tunnel failed, response 403` |
| `network_proxy` on | `full` | `unix_sockets={<path>=allow}` | blocked `EPERM` | reached | via proxy |
| `network_proxy` on | `full` | `allow_local_binding=true` | reached | blocked `EPERM` | via proxy |
| `network_proxy` **off** | `limited` | `domains={example.com=allow}` | **reached** | **reached** | **any host reached** |
| `network_proxy` **off** | `full` | `unix_sockets={<path>=allow}` | reached | reached | any host reached |

Two consequences.

The first is the capability this unlocks for the live gate: a worker profile can
grant real internet access to exactly one host while leaving both loopback paths
into this system closed — the app-server control socket a worker would need to
talk to the runtime directly, and the coordination service's own HTTP port. Each
axis is independently assertable, so the E2E can prove a permitted fetch
succeeds and the two escalation paths fail, in the same run, rather than
inferring confinement from a blanket `networkAccess: false`.

The second is a trap. With `network_proxy` off — its state in 0.154.0, where it
is `experimental` and `false` by default — a profile with `enabled = true` and a
one-host `domains` map enforces **nothing**: the last two rows show every host
reachable, the unix socket reachable with no grant naming it, and loopback open.
The configuration is accepted in full and silently inert. An operator reading
that profile would believe a ceiling exists where there is none. Today's
coordinating-session profile (`live_e2e.py:407`) is in exactly this state,
`mode = "full"` with a `unix_sockets` grant and no proxy feature, which is
harmless only because that session is trusted and means to have full access.

### Unknowns

- Whether `:workspace` plus `runtimeWorkspaceRoots` confines writes exactly as
  today's explicit `writableRoots` literal does is **not** established. Under a
  profile the legacy view reported `writableRoots: []` with the project in
  `runtimeWorkspaceRoots`, described in the schema as materializing
  `:workspace_roots`. This is the correctness question of the whole migration
  and nothing here answers it.
- `permissions` on `ThreadStartParams` and `TurnStartParams` appears only in the
  `--experimental` schema. `compatibility.py:239` already generates with
  `--experimental`, so the gate can see it, but it is an experimental field.
- Enforcement under `network_proxy` is not env-var deep: DNS did not resolve
  in-sandbox and a direct socket to a literal IP failed with `EPERM`, so only the
  loopback proxy is reachable and it applies the allowlist. What is not
  established is whether the feature is stable enough to depend on outside a test
  home, and what a worker that legitimately needs a proxy-hostile protocol does
  under it.

## Why it matters

Scope, isolation, and measurement quality. The live gate is the only evidence
this project accepts for boundary behavior, and it currently runs against a
mutable, developer-specific Codex home on a daemon shared with unrelated
sessions — so a passing run is partly a statement about one machine. Separately,
the boundary is expressed in the one shape the runtime documents as legacy and
reports no provenance for, which blocks #0001 and leaves the project on a
compatibility path that a later CLI can narrow or remove.

This is not filed as a governance-integrity defect: the sandbox literal the
coordinator sends today is enforced, and the boundary regressions prove it on the
pinned version. What is at stake is that the evidence is not reproducible off one
machine, and that the shape cannot express the ceiling #0001 requires.

## What would close this

Ordered so each stage is verifiable before the next depends on it. Stages 1–3
must land together or not at all; a half-migrated boundary is worse than either
end state.

**1. Prove the confinement equivalence before changing anything.**

- `tests/test_runtime_boundary.py` gains a case that starts a thread with
  `permissions: "<profile>"` extending `:workspace` and `runtimeWorkspaceRoots`
  set to the project, and shows an out-of-project write refused and an
  in-project write allowed, under the same assertions the sandbox-literal case
  uses today.
- If confinement is not equivalent, stop and record that here; the rest of this
  issue does not proceed on an unproven boundary.

**2. Pin the wire surface in the compatibility gate.**

- `compatibility.py` asserts `permissions` on `ThreadStartParams` and
  `TurnStartParams`, `activePermissionProfile` on `ThreadStartResponse`, and the
  `NetworkRequirements` keys the profiles use (`mode`, `domains`).
- It asserts the mutual exclusion in the error direction too, or documents that
  it is only observable at runtime.
- A CLI that drops `permissions` or renames `domains` fails startup naming this
  issue, the way the approval-policy set names #0018.

**3. Make the worker boundary profile-shaped.**

- `WorkerPermissions` carries a profile id instead of `sandbox_mode`, keeps
  `approval_policy`, `approvals_reviewer` and `exec_policy`, and its
  `provenance()` reports the profile id and the `extends` chain rather than a
  sandbox mode.
- `enforced_sandbox` is deleted. `thread/start` sends `permissions` and never
  `sandbox`; `turn/start` sends neither, since thread state is sticky.
- `ApprovalPolicy`'s `sandbox_mode` coupling (`coordinator.py:599`,
  `coordinator.py:889`, the `read-only` decline path) is re-expressed against the
  profile, the mismatch check at `coordinator.py:1262` compares profile ids, and
  the `sandbox_mode` threaded into `ApprovalPolicy` at `service.py:594` becomes
  the profile id.
- `session.sandbox_policy` and the `session.started` event report the profile id
  and the `activePermissionProfile` the server returned, and startup fails if the
  server reports a profile other than the one requested, or reports `null`.
- The operator's per-project permissions file names a profile id; an id with no
  definition in the operator's Codex home fails startup with the same
  fail-closed treatment as an unloaded `exec_policy`.
- An inert network ceiling fails startup. A selected profile that declares
  `domains` or `unix_sockets` while `network_proxy` is off is refused with a
  message naming the feature, on the same principle as a declared `exec_policy`
  that loaded no rules: a ceiling that is written but not enforced is a
  configuration error, not a default.

**4. Give the operator a Codex home, and give the tests their own.**

- `examples/operator/codex-home/config.toml` holds `default_permissions =
  ":read-only"` and the per-project `[permissions.<id>]` profiles, alongside the
  constitutions and rules files already there. It is a source file, never a live
  `CODEX_HOME`.
- `OperatorConfig` gains a codex home path; `cli.py` and `preflight.py` pass it
  through; `default_daemon_socket` derives from it rather than from `Path.home()`,
  or the coordinator starts its own listener with
  `--listen unix://<run-dir>/app-server.sock`. Pick one and state which in
  `docs/architecture.md`; the private listener also removes the shared-daemon
  question from every live run.
- `preflight` reports the resolved codex home, the profiles it found, and whether
  auth is present in it, so a live run fails before a turn rather than during one.

**5. Rebuild the live harness on that isolation.**

- `live_e2e.py` renders `examples/operator/codex-home/config.toml` into a
  temporary `CODEX_HOME` per run, symlinks `~/.codex/auth.json` into it, starts a
  private listener, and passes the home to every `codex exec` judge invocation
  and to the service.
- `_coordinator_permission_overrides` and its six `--config` flags are deleted;
  the coordinating session selects a profile from the rendered home by id.
- The run asserts the temporary home was used — the trust record and sqlite state
  appear there and `~/.codex/config.toml` is unchanged — so a passing run is
  evidence about the configuration under test and not about the developer's
  machine.
- `test_judge_live_gate.py` and `test_installed_smoke.py` use the same rendered
  home, so no live path reads the developer's config.
- The rendered home defines a worker profile with granular network — one
  allowed external host, no `unix_sockets` grant, no `allow_local_binding` — and
  the harness enables `network_proxy` for the run. The E2E asserts all three axes
  from inside a worker session: the allowed host is fetched successfully, a
  second host is refused at the proxy, the app-server control socket is refused,
  and the coordination service's own loopback port is refused.
- The two refusals are asserted as sandbox denials, not as approval denials. A
  worker reaching the control socket or the service port is bypassing the
  judging path entirely, so this is the test that the boundary holds when the
  judge is not consulted at all.
- The coordinating session keeps what it needs under the same feature flag:
  `unix_sockets` for the app-server socket and `allow_local_binding` for the
  service port, both named explicitly rather than inherited from `mode = "full"`.

**6. Make it the documented philosophy, not a test trick.**

- `docs/architecture.md` states the model: the runtime owns the permission
  profile, the operator owns the Codex home that defines it, the coordinator
  selects one by id per project and judges what escalates. The legacy sandbox
  literal is named as the shape this project no longer sends.
- `docs/security.md` records that the boundary's provenance is
  `activePermissionProfile`, that the legacy `sandbox` readback is lossy for
  host-scoped network grants, and that `default_permissions` must be pinned
  because the implicit default follows project trust records.
- `docs/networked-orchestration-e2e.md` and `README.md` describe the isolated
  home as the way live runs are performed, including the auth symlink and the
  `examples/operator/codex-home` source.
- `docs/ROADMAP.md` records this as the precondition for the M3 host-scoped
  ceiling rather than a parallel item.

**7. Live verification, since the offline suite cannot show any of it.**

- An opt-in live run completes with the coordinator selecting a per-project
  profile by id, `activePermissionProfile` non-null and matching for every
  session, no `sandbox` or `sandboxPolicy` sent on any call, and the developer's
  `~/.codex` untouched.
- The same run is repeated on a home with no trust records and one with them, and
  the selected profile is identical both times.
- The granular network assertions above pass in a live run, and the same run with
  `network_proxy` disabled fails at startup rather than passing with an inert
  ceiling.

Not in scope: the production worker ceiling. This issue delivers the shape, the
isolation, and a live harness that can prove a granular network boundary; #0001
decides what the ceiling for a real network-capable worker should be, how an
operator declares it per project, and whether `network_proxy` is depended on
outside a test home.
