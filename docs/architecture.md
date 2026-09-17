# Architecture

The repository contains two adapters over one governance boundary and the
app-server transport. `CoordinatorService` is the supported adapter.
`JudgedSessionSupervisor` is a single-worker test harness (`cli.py`, run as
`python -m codex_coordinator.cli`, not an installed command) that drives the
same boundary in-process without the HTTP service; it is described here because
it shares that boundary, not because it is a way to run workers.

## Session creation and execution

`ProtocolClient` owns JSON-RPC framing and multiplexes responses, notifications,
and server approval requests. `CoordinatorService` is the long-running HTTP
adapter; `JudgedSessionSupervisor` is the harness adapter.

Both take a worker's boundary from operator-owned configuration and send it
explicitly on `thread/start`. `WorkerPermissions` accepts `on-request` or
`untrusted` as the approval policy, the `user` reviewer, and a permission
profile id. `never` is rejected because it would execute unjudged. The
protocol's `AskForApproval` enum lists `untrusted` and Codex CLI 0.154.0 accepts
it as a `thread/start` parameter, though the same CLI removed it as a config
value — one more reason these values travel on the wire rather than sitting in a
file.

## The permission model: who owns which half

The runtime owns the permission profile. The operator owns the Codex home that
defines it. The coordinator selects one by id per project, verifies that the
runtime selected that same one, and judges what escalates past it.

The legacy `sandbox` literal is the shape this project no longer sends. It is
mutually exclusive with `permissions` on the wire, and sending it does not
select a profile — it detaches the thread from the profile system, so the
response's `activePermissionProfile` comes back `null` and the boundary has no
provenance at all. Nor can the literal express a host-scoped network ceiling:
the wire grant type carries one bit where a profile carries a per-domain map, a
per-socket map, and loopback binding separately.

`codex_home` in `operator.toml` names the home. It is operator-owned and outside
every worker- and coordinator-writable root, for the same reason `operator.toml`
and the constitutions are: a session that can write its own permission profile
has no boundary. `examples/operator/codex-home/config.toml` is the worked
example. A home is required exactly when an operator names a profile of their
own; the built-in ids need no definition and mean the same thing in every home,
so nothing is ever quietly resolved from whichever `~/.codex` the machine
happens to have.

Every id is resolved to a concrete boundary at startup, before any thread
exists, because the runtime accepts several configurations it does not enforce:

- An unrecognized key in a profile, or an unrecognized token in its `filesystem`
  map, is ignored rather than refused. `:tmp`, `:temp` and `:system_tmp` all
  parse and do nothing where `:tmpdir` and `:slash_tmp` take effect.
- `:workspace` leaves `/tmp` and `$TMPDIR` writable, which the sandbox literal
  excluded. A writable profile that does not demote both is refused, because it
  is a widening of the old boundary rather than a re-spelling of it.
- A `domains` or `unix_sockets` grant enforces nothing unless
  `[features] network_proxy = true`, and that feature is off by default. A
  declared-but-inert ceiling fails startup on the same principle as a declared
  `exec_policy` that loaded no rules. This one is checked across every profile
  the home defines rather than only the selected ones: such a home is
  misconfigured whichever profile a project names today, and an operator
  reading it believes in a boundary that is not there.
- `default_permissions` must be pinned. Without it the runtime refuses to load a
  home that has a `[permissions]` table at all, and the implicit default follows
  project trust records rather than the file.

The socket follows the home. `default_daemon_socket` derives from the configured
Codex home rather than from `Path.home()`, and `ensure_daemon` starts the daemon
with that `CODEX_HOME`, so an operator whose home is the managed one gets a
daemon on it. `socket_path` overrides that with an already-running listener,
which `ensure_daemon` validates rather than starts.

The live harness uses the override, and runs `codex app-server --listen
unix://<run-dir>/app-server.sock` itself. The shared daemon is not an option
there: `codex app-server daemon start` requires the managed standalone install
at `$CODEX_HOME/packages/standalone/current/codex`, which a rendered home has no
business containing, and lending it the developer's would put the run back on
their install — the thing the isolation exists to prevent. A private listener
needs nothing but the home, and removes the shared-daemon question from the run
entirely.

Nothing inside a worker project is read. `[worker_permissions]` in
`operator.toml` maps a project to a permissions file beside `operator.toml`,
under the same ownership rules as a constitution and outside every worker- and
coordinator-writable root; a declaration at a root governs the projects beneath
it, and the most specific one wins. Any key may be omitted, falling back to
`worker_approval_policy` and then to `on-request` with `workspace-write`. A
worker therefore cannot widen its own boundary for a later session by writing a
config file into the root it is allowed to write, which is the escalation the
earlier project-owned arrangement left open.

Under `untrusted` the runtime raises an approval request before anything it does
not already trust; under `on-request` a worker that never asks is never judged.
The profile is unchanged either way. Worker prompts are never the mechanism. A managed
thread is then registered with an immutable session ID, canonical project root,
and `ApprovalPolicy`, and `session.started` records the source and digest of the
permissions the session was derived from, so an audit can compare one session's
boundary against the next.

`thread/start` carries the profile id and nothing else about the boundary. No
turn carries one at all: thread state is sticky, and the turn-level fields are
documented as overriding "for this turn and subsequent turns" rather than as a
per-turn re-imposition, so re-sending a boundary would only reassert state that
never lapsed. Startup fails if the server reports a different profile than the
one requested, or reports none.

A worker profile gives the project as the only writable root, no network, and
neither temporary root writable — `filesystem = { ":tmpdir" = "read",
":slash_tmp" = "read" }` is the profile spelling of the literal's
`excludeTmpdirEnvVar` and `excludeSlashTmp`, keeping the reads the literal also
allowed. The app-server's OS sandbox is the execution-time boundary, so the
restriction also applies to subprocesses, standard temporary-file APIs,
symlinks, and other indirect effects; `tests/test_runtime_boundary.py` runs one
escape probe under both shapes and compares them axis by axis. No worker-authored
file feeds this boundary.

## Unified approval boundary

`ApprovalPolicy` normalizes a request before either adapter exposes it to a judge or
pending queue. It accepts only the three known app-server approval methods and their
known fields. Required identity and correlation fields must have exact types. Paths
are made absolute relative to the registered project, resolved canonically, and
checked with path-component containment rather than string prefixes.

The complete normalized case is recursively immutable: nested mappings are
read-only and nested collections are tuples. This sealed case is the authoritative
input to every later constraint check. Judge prompts, audit records, and live events
receive detached JSON-compatible copies, so mutation by any downstream consumer
cannot change the evidence retained for authorization. A mutation exception in the
harness judge path is treated like any other judge failure and denies the request.

Permission objects have a closed schema. Requested values must fit an immutable,
trusted allowlist, and a judge-proposed permission response must be a subset of both
the normalized request and that allowlist. Commands asking for additional sandbox,
network, or persistent policy authority are denied because a plain command response
cannot safely narrow those capabilities.

For commands, an explicit `availableDecisions` list is also a response ceiling:
`approve_once` can produce `accept` only when `accept` was offered, and
`approve_session` can produce `acceptForSession` only when that exact choice was
offered. Session-wide approval is off by default and additionally requires trusted
service startup or embedding code to enable it. Unavailable verdicts are denied,
not converted to a different approving response. A judge can always deny or narrow
a permission request, but cannot expand its permission or lifetime ceiling.

The harness adapter calls a `Judge` directly. In service-owned mode, the live
broker also invokes an independent restricted judge and rejects HTTP verdicts;
explicit external mode instead accepts verdicts over HTTP. Both modes emit a
normalized `approval.requested` event.

## Deciding a case by code

Between normalization and the judge sits `ApprovalPolicy.decide_by_code`. Both
adapters call it, so the live broker and the harness handler decide
identically. It returns `None` only when nothing in the trusted boundary can
settle the case, which is the only path that spends a judge call. Otherwise it
returns a `CodeDecision`: `approve_once` or `deny`, the rule that decided it in
the words recorded as the reason, and the event that records it —
`approval.allowed_by_policy` or `approval.declined_by_policy`. A third verdict,
`judge`, means an operator rule escalated a case containment would have decided;
that case proceeds normally and its `approval.requested` event carries the
escalation under `containment`.

### In-project file changes

A worker edits its own project by right. The authority is already enforced twice
before this step: the permission profile makes the project the only writable root with
no network, and `normalize_path` resolves every change path against the
registered project and raises if it lands outside, which the adapters answer
with `decline` and an `approval.rejected` event. `_decide_file_change`
therefore sees only in-project paths, and under `workspace-write` it accepts
them as a single-turn `accept` recorded exactly as a rule-allowed command is:
the same normalized evidence, no `approvalId`, no pending approval, no model
call, plus a `containment` record naming the rule and every normalized path.

What it will not decide, it declines rather than judges: a `grantRoot`, which
asks for standing authority over a directory the sandbox never grants. Under
`read-only` a file change is declined with a distinct event and a reason naming
the sandbox mode; `read-only` is the inspection-only mode, so there is no write
authority to approve, and the misleading `filesystemWriteRoots: []` ceiling is
never shown to a judge for a file change. The one exception that reaches a judge
is an operator escalation rule (below).

This step is provisional, and issue #0018 tracks removing it. It exists only
because the pinned CLI has no approval policy that separates an in-project file
change from a command — `untrusted` raises both, `on-request` leaves the
decision to the worker, `never` executes unjudged, and `granular` has no
file-change category — so a reviewer has to answer the question one way or
another. `compatibility.py` asserts the complete approval-policy and granular
category sets, so a CLI that gains such a policy fails the startup gate rather
than leaving this logic in place unexamined. The reasoning is recorded in
`docs/security.md`.

## Deterministic allow for mundane project-local commands

For commands, `decide_by_code` delegates to
`ApprovalPolicy.deterministic_allow`. It applies only to a normalized command
approval that asks for nothing beyond running the command — no network
approval context, no additional permissions, `accept` among the offered
decisions — and only when the project's `WorkerPermissions` carry an
`ExecPolicy`. That policy is loaded by `OperatorConfig` from the rules file a
project's permissions file names under `exec_policy`, resolved beside that
file, validated like a constitution, and never read from inside a project.

`execpolicy.py` parses the file as a strict subset of Codex's own `prefix_rule`
syntax (so `codex execpolicy check --rules` lints it) and evaluates a request
itself: it unwraps `<shell> -lc "<script>"`, splits the script on `&&`, `||`,
`;`, `|`, newline, and the `if`/`then`/`else`/`fi` words, refuses any other
punctuation or expansion character, requires every simple command to match an
allow rule by argv prefix, allows only `exit N`, `true`, and `false` without a
rule, and requires every argument to resolve inside the registered project
relative to the request's working directory. Any failure returns `None` and the
request proceeds to the judge exactly as before. A match produces
`{"decision": "accept"}` and an `approval.allowed_by_policy` event with the
same evidence an approval carries plus the rule provenance; no pending approval
and no judge task are created. A `proposedExecpolicyAmendment` in the request
is validated by normalization and otherwise ignored, so the runtime's policy is
never amended by this path. Both adapters share the step; the harness handler
reports it through `on_decision` with the reason prefixed `allowed by exec
policy`.

The same file carries the one opt-in that sends an in-project file change to a
judge. A `prefix_rule` whose program is the reserved `codex-coordinator-file-change`
and whose decision is `prompt` names project-relative paths — a file, or a
directory and everything beneath it — whose changes escalate. The reserved name
is not a program: no rule may allow it, `evaluate` never matches it, and a
command spelled that way is judged like any other unmatched command. Codex's own
parser reads such a rule as a command that prompts the user, so the file still
lints with `codex execpolicy check --rules`. Everything the coordinator cannot
fully account for fails startup the same way a malformed allow rule does: a
`forbidden` decision, a `prompt` decision on anything but the reserved program,
an absolute or `..`-bearing path, a pattern that is not exactly
`[<reserved>, [paths]]`, and `match`/`not_match` examples that do not hold.

The decision to evaluate rules here rather than hand them to the runtime is
recorded with its evidence in `docs/security.md`: the runtime unwraps the
wrapper and evaluates chains per command, but offers no per-thread way to load
rules, reads worker-writable project rules (#0017), ignores paths in its prefix
match, and does not decide the guard shape workers issue.

## Refusing a worker project that carries Codex rules

The Codex runtime loads execpolicy rules from `<cwd>/.codex/rules` at thread
start; `--strict-config` rejects every candidate key for turning that off, and
an `allow` rule found there suppresses the approval request before anything
reaches this codebase. One in-sandbox write would therefore remove judging from
every later session of that project.

`find_worker_project_rules` walks a worker project without following symlinks
and reports the first `.codex/rules` entry, `*.rules` file under a `.codex`
directory, or symlinked `.codex` the scan cannot see through. The contents are
never read: presence is the finding, and an unfinishable scan is refused rather
than passed. The walk retains a finite one-million-entry ceiling so generated-
code-heavy repositories remain usable without making pathological traversal
unbounded. `CoordinatorService.start_session` and the harness's
`JudgedSessionSupervisor.start` call it before `thread/start`, both callers call
it again before every `turn/start` on an existing thread, and
`codex-coordinator-preflight` calls it when validating a project. The service
emits `session.project_rules_refused` naming the path and answers
`POST /sessions` with HTTP 400 carrying the same text. The rule is about worker
roots only; the coordinator's own project and the operator directory are not
worker roots and are not scanned. Like the file-change step above, the refusal
is provisional: it is the only remedy the pinned CLI allows, and issue #0019
tracks replacing it with a per-thread ignore switch once one exists and is
proven live.

## Two-tier constitution

`Constitution` holds one overall `PolicyDocument` and a mapping of canonical
project directories to their own documents. `for_project` selects the most
specific entry at or above a project; `trusted_policy` returns the overall
document plus that one project document and nothing else, so a judge for one
project cannot read another project's policy. Service-mode configuration always
sets `require_project_policy`, so an uncovered project raises
`PolicyUnavailable`, which the judge converts to a denial without calling a
model, and `start_session` refuses the project outright. The flag exists because
the harness builds a one-tier `Constitution.single` from `judge_policy` text
and has no per-project documents to require; that setting has no other consumer.
`provenance` returns the source path and text digest of each tier for the audit
record. Tier selection is keyed on the
registered project path, never on request content, and the keys are the
canonical paths the operator config validated. In all paths, invalid
responses, judge errors, ambiguity, conflicting evidence, and ceiling violations
fail closed.

## Identity and event isolation

Unknown or missing thread IDs are declined before an approval ID or future is
created. Their request bodies are not printed or added to the event log. The broker
keeps only a non-sensitive in-memory count. Notifications and correlated item
evidence are likewise retained and emitted only for managed threads.

Each pending approval stores its immutable originating registration. Resolving it
requires the matching `sessionId`; an approval from another session cannot consume
or resolve it.

## Untrusted evidence

Worker messages, files, diffs, summaries, command descriptions, reasons, and all
approval fields are untrusted evidence. The judge prompt separates that
evidence from trusted instructions, but prompt construction is not the security
boundary. Decisions bind to normalized fields and registered identity.

Audit events record `declaredIntent` separately from `enforcedCapabilities`. The
former may contain worker-authored prose; the latter is derived solely from trusted
registration and runtime sandbox policy. A third field, `policy`, records which
constitution tiers the judge was given by source and digest; it is derived from
trusted configuration and is absent in external mode, where the service runs no
judge. A fourth, `assignment`, records the turn's task descriptor by digest,
turn number, and source; it comes from the prompt the coordinator sent, not from
the request. The judge prompt carries the normalized `declared_intent` alongside
the raw request so the two constitutions are applied to what the coordinator
actually parsed, not only to the worker's own wording, and carries the
assignment in its own tier so the necessity test has a subject the worker did
not write.

## Deliberate remaining scope

The service still binds to loopback and has no HTTP authentication or
durable state. Approval timeouts and bounded in-memory event retention are
implemented. Remaining medium, low, and
deferred issues remain separately tracked; they do not weaken request normalization
or the execution boundary described here.

Most tests use fake clients and local loopback HTTP. One regression starts a real
local `codex app-server --stdio` process without model inference and proves that a
benign-looking unit-test command cannot write outside its project through a standard
temporary-file API, subprocess, or symlink.
