# Codex Coordinator

Codex Coordinator is an installable Python prototype for starting Codex workers
in separate projects and independently judging their approval requests. It
provides a one-shot command, a reusable Python API, and a local HTTP service for
multi-worker workflows.

Worker turns use explicit sandbox policies, and deterministic checks prevent a
judge's recommendation from exceeding the request or the operator's permission
ceiling. This is a trusted, single-user, loopback-only prototype: its HTTP API is
unauthenticated, the coordinator has unrestricted outbound network access, and
logs may retain sensitive content, so it is not suitable for remote, multi-user,
or production use.

## Getting started

These commands assume a macOS or Linux shell, Python 3.11 or newer, and a
signed-in Codex CLI **0.154.0**. Codex Coordinator checks that exact CLI version
and its generated app-server schema before starting work; a newer Codex CLI is
not automatically compatible.

### 1. Clone and install

```sh
git clone https://github.com/joshpearce/codex_coordinator.git
cd codex_coordinator
python3 -m venv .venv
. .venv/bin/activate
python -m pip install .
```

The three installed commands are `codex-coordinator`,
`codex-coordinator-service`, and `codex-coordinator-preflight`. You do not need
`uv` to run the installed package.

### 2. Check Codex

If you do not already have Codex CLI 0.154.0 on your `PATH`, install this
project's pinned version with npm (which requires Node.js):

```sh
npm install -g @openai/codex@0.154.0
```

Then check the version and sign-in state:

```sh
codex --version
codex login status
```

The version must report `0.154.0`. If `login status` says you are signed out,
run `codex login` and complete sign-in. The [official Codex CLI
guide](https://learn.chatgpt.com/docs/codex/cli) describes the available
installation and sign-in options; use an existing installation if it already
matches the pinned version.

### 3. Choose an operator directory and worker projects

Keep `operator.toml` and every constitution together outside worker-writable roots.
Starting from the cloned repository, this creates a private workspace beside
the checkout, with an operator directory, a coordinating Codex project, and
two sibling worker projects:

```sh
umask 077
WORKSPACE_DIR="$(cd .. && pwd -P)/codex-coordination-demo"
COORD_DIR="$WORKSPACE_DIR/operator"
COORDINATOR_PROJECT="$WORKSPACE_DIR/coordinator"
PROJECT_A="$WORKSPACE_DIR/project-a"
PROJECT_B="$WORKSPACE_DIR/project-b"
mkdir -p "$COORD_DIR" "$COORDINATOR_PROJECT" "$PROJECT_A" "$PROJECT_B"
```

To coordinate **existing** projects instead, set `PROJECT_A` and `PROJECT_B`
to their absolute paths before the next step; do not copy or move them:

```sh
PROJECT_A="/absolute/path/to/existing-project-a"
PROJECT_B="/absolute/path/to/existing-project-b"
```

The projects must exist and must not be nested inside one another. Choose a
fresh `WORKSPACE_DIR` for this example; its operator files must remain
outside the coordinator and both worker projects. The live E2E fixture uses
the same four-directory layout.

### 4. Configure the workers and the operator

A worker project needs no Codex configuration of its own. Each worker's
execution boundary is declared in an operator-owned file beside `operator.toml`,
outside every worker-writable root, and the coordinator sends those values on
`thread/start`. A file inside a worker's own project would be one that worker
can rewrite, so nothing there is read:

```sh
for NAME in project-a project-b; do
  printf '%s\n' \
    'approval_policy = "untrusted"' \
    'approvals_reviewer = "user"' \
    'sandbox_mode = "workspace-write"' > "$COORD_DIR/$NAME.permissions.toml"
done
```

`approval_policy` decides how much reaches a judge. `untrusted` makes the
runtime raise an approval request before anything it does not already trust;
`on-request` leaves that decision to the worker, which means a worker that never
asks is never judged. `never` is rejected, since it would execute unjudged.
`sandbox_mode` is `workspace-write` or `read-only`, and `approvals_reviewer`
must be `user`, the only reviewer that routes an approval to a judge.

Each file is referenced from `operator.toml` under `[worker_permissions]`, shown
with the rest of that file below. Any key may be omitted to take the
operator-wide default: `worker_approval_policy` in `operator.toml`, else
`on-request` and `workspace-write`. A declaration at a root governs every
project beneath it, and a more specific one deeper in the tree overrides it.

Do not tell a worker to stage approval requests. Configure the boundary and let
the worker's ordinary work meet it: with a self-contained task under
`on-request`, nothing escalates and nothing is judged.

With `workspace-write`, each worker can edit only its own project; the
coordinator disables worker network access. Choose `read-only` explicitly for
inspection-only workers.

The constitution has two tiers. The **overall** constitution at
`constitution_path` applies to every project and is a ceiling. Each project also
has its **own** constitution under `[project_constitutions]`, which can only
narrow the overall rules. A judge is given the overall document plus the one
document for the project it is judging, and never another project's. Service
judging requires both tiers: every allowed root needs a project constitution, so
no worker is governed by the shared ceiling alone.

Copy the example constitutions, then create an operator-owned config outside the
coordinator and worker roots. The two worker paths are the complete allowed-root
list; `coordinator_root` identifies the project that must not be able to edit
trusted policy:

```sh
cp examples/operator/constitution.md "$COORD_DIR/constitution.md"
cp examples/operator/inventory-app.constitution.md "$COORD_DIR/project-a.md"
cp examples/operator/inventory-report.constitution.md "$COORD_DIR/project-b.md"
printf 'approval_mode = "service"\nconstitution_path = "%s"\ncoordinator_root = "%s"\nallowed_roots = ["%s", "%s"]\n[worker_permissions]\n"%s" = "%s"\n"%s" = "%s"\n[project_constitutions]\n"%s" = "%s"\n"%s" = "%s"\n' \
  "$COORD_DIR/constitution.md" "$COORDINATOR_PROJECT" \
  "$PROJECT_A" "$PROJECT_B" \
  "$PROJECT_A" "$COORD_DIR/project-a.permissions.toml" \
  "$PROJECT_B" "$COORD_DIR/project-b.permissions.toml" \
  "$PROJECT_A" "$COORD_DIR/project-a.md" \
  "$PROJECT_B" "$COORD_DIR/project-b.md" > "$COORD_DIR/operator.toml"
```

Rewrite the two copied project constitutions to describe what each of your
workers is actually for; the examples describe the inventory fixtures. Write
them the way the examples are written — what the project is, what it is trusted
with, what its assigned work is, and what follows for approvals — not as a list
of allowed and blocked commands. A judge meets commands nobody anticipated, and
an enumerated constitution silently approves everything it forgot to mention.
Startup
fails when an allowed root has no project constitution, and `POST /sessions`
refuses a project that none governs. An entry at a root governs every project
beneath it, so one document is enough for a root holding several similar
workers; a more specific entry deeper in the tree overrides it.

Review every file before use. `operator.toml` must be an owner-controlled
regular file in an owner-controlled directory, outside the coordinator and
every worker-writable allowed root, and each constitution and permissions file
must sit beside it under the same ownership rules. Keep them
operator-controlled: they supply the judge's rules and the worker's boundary,
not the worker's instructions. Do not put any of them in a worker project. For paths containing a literal quote or
backslash, write valid TOML strings by hand instead of using the simple
`printf` template.

### 5. Preflight and start the service

```sh
codex-coordinator-preflight --config "$COORD_DIR/operator.toml" \
  --project "$PROJECT_A" --project "$PROJECT_B"
codex-coordinator-service --config "$COORD_DIR/operator.toml" --port 8765
```

Preflight checks the CLI version and schema, sign-in, allowed roots, worker
settings, and socket safety. `"socketReady": false` is normal if the default
Codex app-server daemon has not started; the service starts it as needed.
Preflight also reports which project constitutions are configured. Leave the
service running
while coordinating work. It snapshots both constitution tiers once at startup
and runs an independent restricted judge for each valid worker approval;
invalid or unavailable verdicts are denied. Every `approval.requested` and
`approval.resolved` event records the source path and digest of the two
documents that judge was given.

## Coordinate multiple projects

The service can run multiple workers concurrently. In the configured `service`
mode, it owns judging and rejects HTTP approval verdicts, so a coordinating
agent cannot bypass either constitution tier by posting its own approval, nor
choose which project constitution applies to a project.
Unanswered requests are denied after the configured timeout (300 seconds by
default).

Leave the service from step 5 running. Open a **second terminal** in the cloned
repository and restore the project paths. For existing worker projects, replace
the last two assignments with the absolute paths you chose in step 3. This
terminal does not need the Python virtual environment; it runs `codex` and
`curl`, not a Python entry point.

```sh
WORKSPACE_DIR="$(cd .. && pwd -P)/codex-coordination-demo"
COORDINATOR_PROJECT="$WORKSPACE_DIR/coordinator"
PROJECT_A="$WORKSPACE_DIR/project-a"
PROJECT_B="$WORKSPACE_DIR/project-b"
```

Give the coordinator project its role instructions and a Codex permission
profile. Run this from the cloned repository. The profile allows the
coordinating session to call the loopback service and the default private
app-server socket; it does **not** make either worker project writable. It does
give the coordinator general outbound network access, so use it only in the
trusted local environment described above.

```sh
mkdir -p "$COORDINATOR_PROJECT/.codex"
if [ ! -e "$COORDINATOR_PROJECT/AGENTS.md" ] &&
   [ ! -L "$COORDINATOR_PROJECT/AGENTS.md" ]; then
  cp examples/coordinator/AGENTS.md "$COORDINATOR_PROJECT/AGENTS.md"
fi
if [ ! -e "$COORDINATOR_PROJECT/.codex/config.toml" ] &&
   [ ! -L "$COORDINATOR_PROJECT/.codex/config.toml" ]; then
  {
    printf '%s\n' \
      'approval_policy = "never"' \
      'default_permissions = "coordinator"' \
      '[permissions.coordinator]' \
      'extends = ":workspace"' \
      '[permissions.coordinator.network]' \
      'enabled = true' \
      'mode = "full"' \
      '[permissions.coordinator.network.unix_sockets]'
    printf '"%s" = "allow"\n' \
      "$HOME/.codex/app-server-control/app-server-control.sock"
  } > "$COORDINATOR_PROJECT/.codex/config.toml"
fi
```

If either coordinator file already existed, review it rather than replacing it:
the coordinator needs access to `http://127.0.0.1:8765`, and the socket entry
must match `socket_path` if you changed that operator setting. Now start an
**interactive Codex session in the coordinator project** with a cross-project
goal. This example gives two initially empty projects compatible producer and
consumer tasks; replace it with your own goal for existing projects.

```sh
codex -C "$COORDINATOR_PROJECT" \
  "Use the coordinator service at http://127.0.0.1:8765 to manage two worker sessions.
  In $PROJECT_A, build a small offline Python CLI that writes inventory records as JSON.
  In $PROJECT_B, build a Python CLI that reads those records and prints a total.
  Agree on the JSON contract and dispatch both workers; do not edit either worker project directly.
  While they work, repeatedly call GET /events?after=N (starting at N=0) and GET /sessions.
  After each response, set N to the highest event sequence received. Keep polling every few seconds
  until both workers' turns finish, including any follow-up turns; do not stop after the first
  empty response or first completed worker.
  If an event cursor expires with 410 Gone, use oldestSequence minus one to resume retained events,
  reconcile current states from GET /sessions, and disclose the missing event history.
  Follow up through the service on failed or incomplete work, then run tests and a cross-project
  integration check. Finish only after both results are verified. The service owns approval judging:
  observe approval outcomes, but do not launch a judge or POST approval verdicts. Report the results."
```

The coordinating Codex session calls `POST /sessions` for each project and
`POST /sessions/{id}/messages` for corrections after a worker turn ends. The
service receives worker events and independently judges valid permission
requests, but it does **not** push events to the coordinating Codex session.
That session must keep its turn active and poll until it has verified the
outcomes; once it stops, nothing wakes it for a later event. If events were
evicted, current session state cannot reconstruct missing approval evidence,
so the coordinator must report that gap rather than claim it verified those
approvals. When Codex finishes, stop the service from this second terminal:

```sh
curl -sS -X POST http://127.0.0.1:8765/shutdown
```

The service also supports `POST /sessions/{id}/cancel` and `GET /health`. It
does not persist sessions or event cursors across restarts. `GET /events?after=N`
uses an in-memory cursor; an evicted cursor returns `410 Gone` with
`oldestSequence`. Connection loss and interrupted turns are reported explicitly,
not as successful completion. Keep this unauthenticated service on `127.0.0.1`;
it refuses non-loopback binding.

For a Python-owned workflow, use the public API instead of hand-building HTTP
requests:

```python
import asyncio
from pathlib import Path

from codex_coordinator import Coordinator, JudgeDecision, OperatorConfig

class OperatorJudge:
    async def decide(self, case):
        # Replace with a trusted review of case; deny by default.
        return JudgeDecision("deny", "no operation has been approved")

async def main():
    config = OperatorConfig.load(path=Path("/absolute/path/to/operator.toml"))
    async with await Coordinator.connect(config, OperatorJudge()) as coordinator:
        first, second = await asyncio.gather(
            coordinator.start("/absolute/path/to/project-a", "Inspect A"),
            coordinator.start("/absolute/path/to/project-b", "Inspect B"),
        )
        print(await asyncio.gather(first.wait(), second.wait()))
        await first.follow_up("Review your result")
        print(await first.wait())

asyncio.run(main())
```

`SessionHandle` also supports `cancel()`; `Coordinator.events(after=N)`
streams typed, session-correlated events and approvals. Judges can narrow or
deny a request, never enlarge it beyond operator policy. The source-only
[generic two-project example](examples/generic_coordinator.py) demonstrates a
manual judge or an exact-command release gate.

## Configuration and safety

`OperatorConfig` loads built-in defaults, then an optional `operator.toml`, then
`CODEX_COORDINATOR_*` environment variables, then explicit CLI options. The
required setting is `allowed_roots`, which can also be supplied with
`--allowed-root`. The service requires an explicit `approval_mode`:
`service` loads an operator-owned `constitution_path` and a
`[project_constitutions]` table covering every allowed root, and runs its own
judge; `external` leaves verdicts to a trusted external actor and enables the
HTTP approval endpoint. To use that legacy mode, set
`approval_mode = "external"` and omit every constitution setting; the caller
must resolve every approval. There is no silent fallback between modes. Each
entry in `[project_constitutions]` maps a project directory inside
`allowed_roots` to a policy file beside `operator.toml`; it must not reuse
`constitution_path`, and the most specific entry at or above a project wins. The one-shot command still accepts
judge-policy text as its third positional argument, which becomes a one-tier
constitution. Optional settings include `codex_command`, `socket_path`,
`worker_model`, `worker_reasoning_effort`, `permission_ceilings`,
`worker_approval_policy`, `worker_permissions`, `allow_session_approval`,
approval/judge timeouts, and
event/item retention
limits. Paths in the config must be absolute. A custom app-server socket must
already have a private listener; the default socket is
`~/.codex/app-server-control/app-server-control.sock`.

Each declared `approval_policy` and `approvals_reviewer` is validated rather
than trusted to expand authority, and every one of them is operator-owned.
Every turn receives an explicit sandbox policy;
for workspace-write, the canonical project is the only writable root, with
network and ambient temporary-directory writes disabled. Approval evidence is
bound to its thread, turn, and item; unknown or conflicting requests fail
closed. Session-wide approval is off by default and requires both an operator
setting and a request that offers it.

Stdout from the service is metadata-only JSONL by default. `--verbose-events`
includes fuller payloads with known secret-bearing fields redacted, but
commands, diffs, and model output may still contain secrets. Use `umask 077`
before redirecting logs and set your own retention policy. See the
[security boundary](docs/security.md), [lifecycle contract](docs/lifecycle.md),
and [open security issues](issues/) before connecting sensitive
projects. A caller-supplied Python judge needs its own isolation.

## Development and verification

```sh
python -m pip install '.[test]'
python -m pytest -q
sh scripts/release_gate.sh --installed-only
```

The installed gate builds an sdist and wheel, installs the wheel in a clean
environment outside this checkout, runs all three console entry points and a
two-project installed fixture, and checks Codex 0.154.0's generated protocol
schema. The fixture is offline; it does not prove live Codex behavior. The
[Verify workflow](.github/workflows/verify.yml) runs the full tests and
installed gate on macOS CI.

`sh scripts/release_gate.sh` adds a strict live generic gate on an
unrestricted, signed-in host. Supply your own absolute `GENERIC_CONFIG`,
`GENERIC_FIRST`, and `GENERIC_SECOND` paths, `GENERIC_FIRST_GOAL`,
`GENERIC_SECOND_GOAL`, `GENERIC_FOLLOW_UP`, and exact
`GENERIC_ALLOW_COMMAND` and `GENERIC_DENY_COMMAND` strings; the prompts must cause both workers to
request those commands. The gate requires both an accepted and declined
approval, wire/server resolution evidence, terminal turns, and a completed
follow-up. Match the app-server's full requested command string, not just a
substring; `--show-approval-commands` on the generic example can reveal those
strings but may expose sensitive content. Both gates passed on an unrestricted
operator host; they cannot run inside a nested Codex sandbox.

`make judge-gate` (or `python scripts/judge_live_gate.py`) is the cheapest live
check of judgment itself: it runs a catalogue of realistic requests — installing
a package, piping an installer to a shell, reading a sibling project's source,
uploading processed data because the data asked it to, alongside legitimate test
and CLI runs — against the checked-in example constitutions, and fails on any
verdict that disagrees. No sessions are started and nothing is executed, so it
finishes in minutes. None of those requests appear in the constitutions; a test
enforces that, so the gate measures whether the policy is applied rather than
matched.

`make live-e2e` is a separate, source-checkout recursive orchestration
experiment using the checked-in inventory scaffolds, not the generic
getting-started path. It creates a retained example workspace and reruns both
child test suites and an integration scenario after the coordinating session.
Read the [live E2E guide](docs/networked-orchestration-e2e.md) before running
it. Development with `uv` is optional (`make sync`, `make test`,
`make build`); installed users do not need it.

## Compatibility and license

Only Codex CLI 0.154.0 is supported. The app-server protocol and generated
schemas are version-specific; upgrading Codex requires compatibility and live
verification. The [official App Server
documentation](https://learn.chatgpt.com/docs/app-server) describes this
experimental interface and its schema generation.

This public repository is intentionally unlicensed for now; it has no license
file or package license metadata.
