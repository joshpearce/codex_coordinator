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
under the same ownership and location rules as a constitution. A project whose
tree carries Codex execpolicy rules of its own is refused a session outright;
see "A worker project may not carry Codex rules" below. The configured
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
and coordinator-writable root, so a worker cannot widen its permission profile
for a later session by writing inside the root it is allowed to write. The Codex
home that defines those profiles is held to the same rule. The current
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

## The judge is told what the worker was asked to do

Both constitutions test necessity first: an action must be necessary for the
task the child was assigned. Evaluating that needs a statement of the task, and
the only candidates on the approval wire — `reason` and `command` — are written
by the worker whose request is being judged. A compromised worker did not have
to overrule the necessity rule; it supplied the evidence the rule was applied
to (#0002).

A judged case now carries a task assignment. It is the prompt the coordinator
sent for that turn, recorded against the thread before `turn/start` is called,
so the turn's first approval request already has one. Prompts are numbered per
thread, so a follow-up turn replaces the previous turn's descriptor rather than
adding to it. Three authorship tiers reach the judge, separated in the prompt:
the operator's two constitutions, the coordinator's assignment, and the
worker's own request evidence.

A worker cannot author the descriptor it is judged against. The assignment
never crosses the approval wire; it is read from a ledger the coordination path
writes, and `ApprovalPolicy.normalize` rejects any request field it does not
recognize, including one named `assignment`. A worker's `reason` is still
recorded, still shown to the judge, and still only evidence.

Judging fails closed without one. A judge asked to decide a case with no
assignment denies it without invoking a model, the same way it denies a project
no constitution governs: a necessity test with no stated task is not a test.

The descriptor is not operator policy, and the judge is told so. The
coordinating session composes prompts and may relay what a worker reported, so
a worker can influence a *later* turn's assignment even though it cannot author
the one it is judged under. That is why the assignment is a separate tier from
the constitutions rather than folded into them, and it is one more reason the
coordinator's own egress matters (#0007). The static half of the same question —
what kind of worker this is, and what its standing purpose is — belongs to the
per-project constitution.

`approval.requested`, `approval.resolved`, and the by-policy decision events
record the assignment's turn number, source, digest, and length, but not its
text; the text is recorded once by the `session.started` or
`session.turn_started` event that sent it, and the digest joins the two. A
judge is shown the assignment's first 8192 characters — all the ledger keeps —
with the digest and length of the whole and a flag when it was cut, so a very
long prompt neither crowds out the policy tiers it is read beside nor sits in
memory for the life of the session. The live E2E harness fails a run
whose approvals carry no assignment, restate its text, or cite a digest no
prompt in that run produced.

Granularity is the turn. Every request in a turn is judged against that turn's
assignment, so an injected instruction acted on halfway through a turn is
judged against the task the turn began with — which is the point — but the
descriptor cannot distinguish two actions within one turn. Requests are matched
to an assignment by thread and turn order, not by the runtime's `turnId`, which
is not known until `turn/start` returns and so cannot be recorded before the
turn's first request may arrive. A request from an earlier turn arriving after
a follow-up has started is therefore judged against the newer task; both turn
numbers are on the record.

## Inside its project a worker acts by right

A worker edits its own project the way a developer edits a checkout, and that
authority is enforced twice before any judge is involved. The permission profile makes
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

### Why this is decided here rather than configured in the runtime

Answering a question the runtime asks is reviewer policy; deciding what the
runtime may do is its permission system. The line matters, because every rule
this codebase evaluates itself is a rule that can drift from what the runtime
actually enforces. The reason the containment decision lives here is that the
pinned CLI has no setting that stops the question being asked. Its complete
wire approval vocabulary, read from the generated schema, is:

```
AskForApproval = "untrusted" | "on-request" | "never"
               | {granular: {mcp_elicitations, request_permissions, rules,
                             sandbox_approval, skill_approval}}
```

None of those separates an in-project file change from a command. `untrusted`
raises an approval before both; `on-request` leaves the decision to the worker,
so a worker that never asks is never judged; `never` executes unjudged; and
`granular` has no file-change category and was observed letting an unmatched
command run with no approval at all. Codex's rule language offers no help
either: `prefix_rule`, `network_rule`, and `host_executable` are the whole
vocabulary, and none of them is path-shaped. So a reviewer must answer file
changes one way or another, and answering them from the boundary already
configured is the narrowest available answer.

This is tracked as issue #0018 rather than settled, and `compatibility.py`
asserts both sets above, so a CLI that adds an approval policy or a granular
category fails the startup gate and sends an operator back to that issue
instead of silently keeping logic the runtime could now carry. The same
reasoning, with its own probe evidence, is why the coordinator evaluates
command rules itself rather than handing them to the runtime; see below.

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

## A worker project may not carry Codex rules

The Codex runtime loads execpolicy rules from `$CODEX_HOME/rules` and from
`<cwd>/.codex/rules`, where `cwd` is the worker project. Live probing of the
pinned CLI, repeated after this refusal was designed, shows the loading is
unchanged: a thread started in a project containing
`.codex/rules/worker.rules` logs `loaded 1 .rules files in
<project>/.codex/rules` at thread start, and under `approval_policy =
"untrusted"` with a writable permission profile an `allow` rule there
suppressed the approval request entirely — the worker's `cat NOTES.md` ran with
no approval at all. Rules are read at thread start, not per turn, so a worker
cannot change its current session's policy; but one in-scope, in-sandbox write —
the kind of write a judge has every reason to approve, since writing project
files is the assignment — would remove judging from every later session of that
project, with no signal in this codebase's events. `--strict-config` rejects
`exec_policy`, `execpolicy`, and `rules` as unknown app-server fields, and
`--ignore-rules` exists for `codex exec` but not for the app-server, so there is
no setting that turns this off.

The coordinator therefore refuses the project. `CoordinatorService.start_session`
and the harness's `JudgedSessionSupervisor.start` scan the project tree before
`thread/start`, and both scan again before every `turn/start` on an existing
thread, so a project that acquires such a file mid-session gets no further turn. A finding is
a `.codex/rules` entry, any `*.rules` file under a `.codex` directory, or a
symlinked `.codex` the scan does not follow and therefore cannot clear; a scan
that cannot finish within its bound is refused rather than passed. The file's
contents are never read: presence is the finding. The service emits
`session.project_rules_refused` naming the path and answers `POST /sessions`
with HTTP 400 carrying the same text, and `codex-coordinator-preflight` reports
it when validating a project. The check applies to worker roots only; the
coordinator's own project and the operator directory are not worker roots and
are not scanned. The refusal is blunt on purpose: it denies a session to a
project that may have legitimate reasons to carry rules, because the
coordinator cannot tell the legitimate ones from the widening ones without
reading a file it has decided not to trust. If a later pinned CLI offers an
app-server or per-thread switch to ignore project rules, the coordinator sets
it and asserts it loaded, and the refusal stays until that switch is proven
live. That follow-up is tracked as issue #0019.

## The coordinating session's sandbox is declared on its command line

The coordinating session needs one capability its workers must never have: it
must reach the loopback control plane. Codex CLI 0.154.0 does not grant that
from a permission profile written inside the project the session runs in. Live
probing of the pinned CLI shows `codex exec --cd <project>` ignoring a
`[permissions]` profile in `<project>/.codex/config.toml` entirely: ten
consecutive `curl` attempts over thirty seconds, from inside a session started
that way, were refused by `ECONNREFUSED` while the service was listening,
healthy, and idle; the same profile passed as `--config` overrides on the same
command reached the service on the first attempt. Nothing warns that the file
was ignored, and the session keeps the default sandbox, which has no network.

The live harness therefore builds the coordinator profile itself and passes it
on the `codex exec` command line, and the coordinator project ships no Codex
configuration at all. That is the boundary this project already requires of a
worker, applied to the coordinator for the same reason: the one root a session
can write must not be where its own permissions are declared. The runtime
ignoring such a file is the safe direction of the two — a session cannot widen
itself by writing one — but it is not a substitute for keeping the declaration
outside, because the ignoring is unannounced and is not a documented guarantee
of the pinned CLI.

A control plane that is listening and a control plane that is reachable are not
the same claim, and the difference is invisible in the service's own log: every
route that changes anything emits an event, so an idle service and an exited one
both leave a log holding nothing but `service.started`. The harness reports the
distinction rather than leaving it to be inferred — see
`_control_plane_error` in `live_e2e.py`, which names an unreachable control
plane, and whether the service was still running, ahead of the downstream
symptoms that follow from it.

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
deterministic layer as before: the permission profile, which keeps writes inside the
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
is refused by the service and the harness supervisor rather than silently
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
start (which is why such a project is refused a session), that its
`prefix_rule` never constrains paths so a `sed -n` rule
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

Both adapters use the same judge, `OneShotCodexJudge`, whose name describes one
`codex exec` invocation per case rather than the harness. It requests a
deny-by-default filesystem permission profile, grants reads only to Codex's
minimal runtime paths, its resolved installed runtime (and macOS system OpenSSL
configuration if present), and its empty temporary evidence directory. It
disables auxiliary tools and ignores user configuration and project rules. The exact profile has a denied-read regression
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

## The boundary's provenance is the permission profile

The coordinator names a permission profile per project and sends the id on
`thread/start`. The runtime answers with `activePermissionProfile`, and a
session is refused unless that names the profile that was requested. This is the
only provenance the wire carries, and it is why the legacy `sandbox` literal is
no longer sent: sending it does not select a profile but detaches the thread
from the profile system, and `activePermissionProfile` comes back `null`.

The legacy readback is also lossy in the direction that matters. Under a profile
limited to a single host the derived `sandbox` view reports
`networkAccess: true`, so an audit that read that field back could not see a
host-scoped ceiling at all. Under every shape it reports `writableRoots: []`,
with the writable scope living in `runtimeWorkspaceRoots`, so it cannot answer
what a thread may write either.

`default_permissions` must be pinned in the operator's Codex home. The implicit
default follows project trust records rather than the file: probed against the
pinned CLI, the same unconfigured thread resolved to `:read-only` before a trust
record existed for its cwd and to `:workspace` after one did. A home carrying a
`[permissions]` table without `default_permissions` is refused outright, by the
runtime and by this coordinator.

Three further configurations are accepted by the runtime and enforce less than
they read as enforcing, so the coordinator refuses each at startup rather than
discovering it through a worker: an unrecognized key or `filesystem` token,
which is ignored rather than rejected; a writable profile that does not demote
`:tmpdir` and `:slash_tmp`, which leaves `/tmp` and `$TMPDIR` writable where the
previous boundary did not; and a `domains` or `unix_sockets` grant declared
while `[features] network_proxy` is off, which enforces nothing at all while
reading as a ceiling. The first two are rules about a profile a worker is given,
so they are applied where one is selected. The third is a rule about the home:
it is applied to every profile defined there, because a home carrying an inert
ceiling misleads its reader whichever profile is selected today.

A fourth trap is not refusable, only documented: `network.mode` grants nothing.
Probed with the proxy on, `mode = "full"` and `mode = "limited"` behave
identically on all three axes, and each axis is governed only by its own grant —
so `mode = "full"` with no `domains` map reaches no external host at all, every
request coming back `403` from the proxy. It reads far wider than it enforces.
Write the axes explicitly and do not rely on the mode to mean anything.
