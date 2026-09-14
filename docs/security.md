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
policy document.

## Inside its project a worker acts by right

A worker edits its own project the way a developer edits a checkout, and that
authority is enforced twice before any judge is involved. The turn sandbox makes
the project the only writable root with no network, and `normalize_path`
resolves every change path against the registered project and refuses anything
outside it. A `workspace-write` file change whose every path clears both is
therefore accepted by code as a single-turn `accept` — no `approvalId`, no
pending approval, no model call — and recorded as `approval.allowed_by_policy`
with the same evidence an approval carries plus the containment rule and every
normalized path. Nothing about the sandbox widens: writes still land only in the
project and the network stays off.

Judging is reserved for what leaves the project: reads of other projects or
operator files, network reach, new dependencies, environment changes, and
commands whose effect cannot be seen. This is a governance-quality property, not
a convenience. Judging writes the sandbox has already confined spends the
judging budget where the deterministic layer decides the outcome anyway and adds
worker-authored diffs to the untrusted-evidence surface for no decision.

What containment cannot decide is declined, not judged. A path outside the
project is refused by normalization and recorded as `approval.rejected`; so is a
change list that cannot be correlated to its item. A `grantRoot` asks for
standing authority over a directory rather than for this change, which the turn
sandbox never grants, so it is declined. Under `read-only` every file change is
declined with a reason naming the sandbox mode and a distinct
`approval.declined_by_policy` event. `read-only` is the inspection-only mode:
there is no write authority to approve, and the misleading
`filesystemWriteRoots: []` ceiling — which previously made a correct judge
refuse every write and deadlocked such workers — is never shown to a judge for a
file change at all.

The single exception is opt-in and operator-owned. A `prefix_rule` in the
project's execpolicy rules file whose program is the reserved
`codex-coordinator-file-change` and whose decision is `prompt` names
project-relative paths whose changes reach a judge anyway: supplied tests,
packaging, anything the operator wants reviewed. A named directory covers
everything beneath it. The reserved name is not a program — no rule may allow
it, and a command spelled that way is judged like any other unmatched command —
and Codex's own parser reads the rule as a command that prompts the user, so the
file still lints with `codex execpolicy check --rules`. A rules file that uses a
decision or shape this evaluator does not fully account for fails startup, the
same as a malformed allow rule.

## Commands decided by operator rule rather than by a judge

The one place commands are named is a separate kind of file: an operator-owned
execpolicy rules file, referenced as `exec_policy` from a project's permissions
file and validated under the same ownership and location rules. It lists the
mundane project-local commands the coordinator decides itself, without a judge:
in the examples, `sed -n` range reads, `rg` searches, and the project's own
`python -m unittest` and `python -m <package> --help` entry points. The
coordinator, not the Codex runtime, evaluates it, and the evaluation is stricter
than the runtime's own: the `<shell> -lc "<script>"` wrapper is unwrapped, every
simple command in a `&&`, `||`, `;`, `|`, newline, or `if … then exit 1; fi`
chain must match an `allow` rule, every argument must resolve inside the
registered project from the request's own working directory, and anything the
evaluator cannot fully parse — redirections, substitutions, expansions,
assignments, background jobs, an unrecognized shell invocation — goes to a
judge. A match yields a single-turn `accept` only, never a session grant and
never the `acceptWithExecpolicyAmendment` a request may offer; the
`proposedExecpolicyAmendment` a worker authors is ignored, so this path cannot
be used to change the rules that govern it. What it rests on is the same
deterministic layer as before: the turn sandbox, which keeps writes inside the
project and disables the network regardless of any decision, and
`normalize_path`, which already rejects a working directory outside the
project. File changes are a different approval method, decided by containment
as described above; the same file names the in-project paths, if any, that the
operator wants judged anyway.
`find` is deliberately not in the example rules because `-exec` runs an
arbitrary program per match. A rule still admits every mode of the program it
names within the project, so an operator should name only programs whose whole
behavior inside the project may go unjudged.

Loading fails closed: a rules file that is missing, not owner-controlled, not
beside `operator.toml`, inside a worker or coordinator root, not in the accepted
subset of the syntax, or whose own `match`/`not_match` examples do not evaluate
as declared is a startup error, and a declared rules file that was never loaded
is refused by the service and the one-shot supervisor rather than silently
meaning "judge everything". Each decision is recorded as an
`approval.allowed_by_policy` event carrying the normalized request, the rule
file's source and digest, and the rule that decided each simple command. A file
change decided by containment is recorded the same way, with a `containment`
record instead of a rule-file one, and a declined one as
`approval.declined_by_policy`.

The Codex runtime's own execpolicy is deliberately not used. Live probing of the
pinned CLI established that the runtime does unwrap the shell wrapper and does
evaluate `&&` chains per command, but also that it has no per-thread
configuration key for rules (`--strict-config` rejects every candidate), that it
reads rules from the worker project's own `.codex/rules` directory at thread
start (#0017), that its `prefix_rule` never constrains paths so a `sed -n` rule
would admit a read of any file on the machine, and that it does not decide the
`if … fi` guard shape workers actually issue. Under `granular` approval an
unmatched command ran with no approval at all, so `untrusted` remains the only
supported wire policy. Note that some scope expansions never reach a judge at all: a
network permission request is rejected by `normalize_permissions` against the
operator ceiling first, so an install attempt reaches the constitution as a
command, which is also how a real worker issues it. Explicit `external` mode
allows a trusted actor to submit verdicts but does not itself run a judge.

## Logging and retention

By default, stdout JSONL contains metadata only. `--verbose-events` opts into
payload output for the source-only live experiment; known secret-bearing field
names are redacted, but commands, prompts, diffs, and model messages can still
contain secrets. Full non-oversized managed events are retained only in memory,
under the limits in [the lifecycle contract](lifecycle.md). Run with `umask 077`
before redirecting stdout and remove logs on an operator-defined schedule.
The live experiment sets a private umask for its generated files. Its
coordinator JSONL log is created mode `0600` and refuses an existing file or
symlink rather than overwriting it.

## Judge isolation

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
