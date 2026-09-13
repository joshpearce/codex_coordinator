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

Keep `operator.toml` and `constitution.md` together outside worker-writable roots.
Starting from the cloned repository, this creates a private workspace beside
the checkout, with an operator directory and two sibling worker projects:

```sh
umask 077
WORKSPACE_DIR="$(cd .. && pwd -P)/codex-coordination-demo"
COORD_DIR="$WORKSPACE_DIR/operator"
PROJECT_A="$WORKSPACE_DIR/project-a"
PROJECT_B="$WORKSPACE_DIR/project-b"
mkdir -p "$COORD_DIR" "$PROJECT_A" "$PROJECT_B"
```

To coordinate **existing** projects instead, set `PROJECT_A` and `PROJECT_B`
to their absolute paths before the next step; do not copy or move them:

```sh
PROJECT_A="/absolute/path/to/existing-project-a"
PROJECT_B="/absolute/path/to/existing-project-b"
```

The projects must exist and must not be nested inside one another. Choose a
fresh `WORKSPACE_DIR` for this example; its two operator files must remain
outside both worker projects. The live E2E fixture uses the same layout and
adds a separate `workspace/coordinator/` Codex project beside the two workers.

### 4. Configure the workers and the operator

Each worker needs `.codex/config.toml`. This creates a workspace-write
configuration only when the file is absent, so it does not overwrite an
existing project's Codex settings:

```sh
for PROJECT_DIR in "$PROJECT_A" "$PROJECT_B"; do
  mkdir -p "$PROJECT_DIR/.codex"
  if [ ! -e "$PROJECT_DIR/.codex/config.toml" ] &&
     [ ! -L "$PROJECT_DIR/.codex/config.toml" ]; then
    printf '%s\n' \
      'approval_policy = "on-request"' \
      'approvals_reviewer = "user"' \
      'sandbox_mode = "workspace-write"' > "$PROJECT_DIR/.codex/config.toml"
  fi
done
```

For existing projects, inspect their files and ensure the approval settings
match exactly. With `workspace-write`, each worker can edit only its own
project; the coordinator disables worker network access. Choose `read-only`
explicitly for inspection-only workers.

Copy the example constitution, then create an operator-owned config outside the
worker roots. The two project paths are the complete allowed-root list:

```sh
cp examples/operator/constitution.md "$COORD_DIR/constitution.md"
printf 'allowed_roots = ["%s", "%s"]\n' \
  "$PROJECT_A" "$PROJECT_B" > "$COORD_DIR/operator.toml"
```

Review both files before use. `operator.toml` must be an owner-controlled
regular file in an owner-controlled directory, outside every worker-writable
allowed root. Keep the constitution operator-controlled too: it supplies the
judge's rules, not the worker's instructions. Do not put either file in a
worker project's `.codex` directory. For paths containing a literal quote or
backslash, write valid TOML strings by hand instead of using the simple
`printf` template.

### 5. Preflight and run a worker

```sh
codex-coordinator-preflight --config "$COORD_DIR/operator.toml" \
  --project "$PROJECT_A" --project "$PROJECT_B"
codex-coordinator --config "$COORD_DIR/operator.toml" \
  "$PROJECT_A" "Inspect this project and report a short summary; do not edit files." \
  "$(cat "$COORD_DIR/constitution.md")"
```

Preflight checks the CLI version and schema, sign-in, allowed roots, worker
settings, and socket safety. `"socketReady": false` is normal if the default
Codex app-server daemon has not started; the one-shot command starts it as
needed. The command prints a JSON report with the worker's final state and any
approval decisions. Its independent one-shot Codex judge is advisory; invalid
or unavailable verdicts are denied.

## Coordinate multiple projects

The service can run multiple workers concurrently. It uses the same
`operator.toml` but **does not supply its own judge** or read `constitution.md`:
an external trusted judge or an operator must read the constitution and each
`approval.requested` event, then post verdicts.
Unanswered requests are denied after the configured timeout (300 seconds by
default).

Start it in one terminal with the virtual environment active:

```sh
codex-coordinator-service --config "$COORD_DIR/operator.toml" --port 8765
```

Open another terminal in the cloned repository and activate its environment.
For the newly created projects, restore the same path variables; for existing
projects, set them to the absolute paths you chose in step 3:

```sh
. .venv/bin/activate
WORKSPACE_DIR="$(cd .. && pwd -P)/codex-coordination-demo"
COORD_DIR="$WORKSPACE_DIR/operator"
PROJECT_A="$WORKSPACE_DIR/project-a"
PROJECT_B="$WORKSPACE_DIR/project-b"
```

Then start two sessions and inspect their progress:

```sh
curl -sS -X POST http://127.0.0.1:8765/sessions \
  -H 'Content-Type: application/json' \
  -d "{\"project\":\"$PROJECT_A\",\"prompt\":\"Inspect project A and report findings.\"}"
curl -sS -X POST http://127.0.0.1:8765/sessions \
  -H 'Content-Type: application/json' \
  -d "{\"project\":\"$PROJECT_B\",\"prompt\":\"Inspect project B and report findings.\"}"
curl -sS http://127.0.0.1:8765/sessions
curl -sS 'http://127.0.0.1:8765/events?after=0'
```

The returned session objects have IDs for follow-ups and cancellation. To
resolve a pending approval, take its `approvalId` and `sessionId` from the
event stream and send a verdict such as `deny`:

```sh
curl -sS -X POST "http://127.0.0.1:8765/approvals/APPROVAL_ID" \
  -H 'Content-Type: application/json' \
  -d '{"sessionId":"SESSION_ID","verdict":"deny","reason":"not approved"}'
```

Replace the two uppercase IDs with the actual values. The local API also
supports `POST /sessions/{id}/messages` after a turn completes,
`POST /sessions/{id}/cancel`, `GET /health`, and `POST /shutdown`.
For example, after a session reaches `completed`, send a follow-up and then
stop the service when finished:

```sh
curl -sS -X POST "http://127.0.0.1:8765/sessions/SESSION_ID/messages" \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"Review your findings and list next steps."}'
curl -sS -X POST http://127.0.0.1:8765/shutdown
```

Replace `SESSION_ID` with that session's ID. The service does not persist
sessions or event cursors across restarts.
`GET /events?after=N` uses an in-memory cursor; an evicted cursor returns
`410 Gone` with `oldestSequence`. Connection loss and interrupted turns are
reported explicitly, not as successful completion. Keep this unauthenticated
service on `127.0.0.1`; it refuses non-loopback binding.

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
`--allowed-root`. The one-shot command accepts the text of `constitution.md`
as its third positional argument for its built-in judge; the HTTP service
does not read `judge_policy` or run a judge. Optional
settings include `codex_command`, `socket_path`,
`worker_model`, `worker_reasoning_effort`, `permission_ceilings`,
`allow_session_approval`, approval/judge timeouts, and event/item retention
limits. Paths in the config must be absolute. A custom app-server socket must
already have a private listener; the default socket is
`~/.codex/app-server-control/app-server-control.sock`.

Worker `approval_policy` and `approvals_reviewer` are validated rather than
trusted to expand authority. Every turn receives an explicit sandbox policy;
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
and [open security issues](docs/issues/issues.md) before connecting sensitive
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
strings but may expose sensitive content. Use `python scripts/judge_live_gate.py`
for the separate live one-shot judge check. Both gates passed on an unrestricted
operator host; they cannot run inside a nested Codex sandbox.

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
