# Supported security boundary

The supported deployment is one trusted operator and trusted local processes
on a single machine. The coordinator's judge and the operator-owned TOML file
are trusted; worker project files, prompts, approval requests, tool output,
and model verdicts are not. The deterministic approval policy validates
requests and prevents judge output from widening the original request or the
operator's permission ceiling.

The HTTP control plane is bound to `127.0.0.1` only, rejects browser `Origin`
headers and non-local `Host` headers, and has request limits. It has **no
authentication**. Any other local process running as the operator can call it;
malicious local processes are outside this threat model. Remote clients,
browser clients, and multiple users are unsupported. No flag enables remote
binding. Authentication, transport protection, and per-user authorization
would be required before supporting those modes.

Projects must be under startup-configured canonical allowed roots before any
session is started for them. Existing symlinks that escape those roots are
rejected. A worker project's own files are never read as configuration; its
boundary comes from an operator-owned permissions file validated at startup
under the same ownership and location rules as a constitution. The configured
app-server endpoint must be a Unix socket owned
by the current user with no group/other permissions, inside an owner-controlled
directory. Workers receive explicit Codex sandbox policies with no network or
ambient temporary-directory write access. The coordinator process itself is
not network-sandboxed; its network privileges must be considered trusted.
Operator TOML must be an owner-controlled regular file outside every
worker-writable allowed root; symlinked or group/other-writable files fail
validation. Service-owned judging also requires a same-directory overall
constitution file, an explicit coordinator root, and a project constitution
covering every allowed root. Every
trusted policy file must sit beside `operator.toml` under the same ownership
rules and outside the coordinator and worker roots, and a project constitution
may not reuse the overall one. All tiers are snapshotted at startup; HTTP
verdict submissions are rejected in this mode.

The two tiers are a scoping control, not just an authoring convenience. The
overall document is a ceiling; a project document may only narrow it. A judge
invoked for one project receives that project's document and no other, so
policy written for a permissive worker cannot be read while judging a sensitive
one, and the operator is not forced to write the union of every worker's needs.
Both tiers are mandatory in service mode, so no worker is governed by the shared
ceiling alone: startup rejects an allowed root with no project constitution,
`POST /sessions` refuses an ungoverned project, and a judge asked about one
denies without invoking a model. Tier selection is derived
from the trusted session registration, never from request content. Both tiers
are recorded by source path and text digest on `approval.requested` and
`approval.resolved`, so an audit can show which documents a verdict was made
under. Precedence between the tiers is prose evaluated by a model: it guides a
judge and does not replace the deterministic path, sandbox, permission, and
session limits.

What escalates into a judged approval is decided by each project's
operator-owned permissions file and the sandbox derived from it, never by
instructing a worker to stage approval requests: worker prompts in the examples
contain no mention of judging, and the example child project trees contain
neither prompts nor Codex configuration. Worker-owned permission configuration
is not supported. Those files live beside `operator.toml`, outside every worker-
and coordinator-writable root, so a worker cannot widen its `sandbox_mode` for a
later session by writing inside the root it is allowed to write. The current
session is likewise unaffected by anything: registration snapshots the boundary
and every turn replays it, and `session.started` records the source and digest
of the permissions used, so an audit can show whether the boundary changed
between two sessions of the same project.

Both tiers are written as principles rather than command lists. That is a
security property, not a style preference: a judge meets requests nobody
anticipated, and an enumerating constitution is silent — which reads as
permissive — on everything it failed to foresee. `scripts/judge_live_gate.py`
holds a catalogue of realistic requests the constitutions deliberately do not
name, and a unit test fails if any of those requests later appears verbatim in a
policy document. Note that some scope expansions never reach a judge at all: a
network permission request is rejected by `normalize_permissions` against the
operator ceiling first, so an install attempt reaches the constitution as a
command, which is also how a real worker issues it. Explicit `external` mode
allows a trusted actor to submit verdicts but does not itself run a judge.

By default, stdout JSONL contains metadata only. `--verbose-events` opts into
payload output for the source-only live experiment; known secret-bearing field
names are redacted, but commands, prompts, diffs, and model messages can still
contain secrets. Full non-oversized managed events are retained only in memory,
under the limits in [the lifecycle contract](lifecycle.md). Run with `umask 077`
before redirecting stdout and remove logs on an operator-defined schedule.
The live experiment sets a private umask for its generated files. Its
coordinator JSONL log is created mode `0600` and refuses an existing file or
symlink rather than overwriting it.

The one-shot and service-owned `codex exec` judges request a deny-by-default filesystem
permission profile, grants reads only to Codex's minimal runtime paths, its
resolved installed runtime (and macOS system OpenSSL configuration if present),
and its empty temporary evidence directory. It disables auxiliary tools and
ignores user configuration and project rules. The exact profile has a denied-read regression
test. Before every judge invocation, the runner probes the same profile with
one allowed read, one unrelated-file read, and execution of the resolved Codex
binary; if the probe cannot run or the unrelated read succeeds, the judge fails
closed. The runtime denied-read regression test and a live `codex exec` judge
decision passed on an unrestricted operator host. The nested sandbox cannot
launch in the restricted development workspace. This check is specific to the
supported CLI and host; keep the fail-closed probe when changing either.
A caller-supplied judge in the Python API can operate on the frozen, minimal
`ApprovalCase` evidence, but the caller is responsible for isolating its own
implementation. This remains a requirement for caller-supplied judges.
