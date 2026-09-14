# Architecture

The repository contains two adapters over one governance boundary and the
app-server transport.

## Session creation and execution

`ProtocolClient` owns JSON-RPC framing and multiplexes responses, notifications,
and server approval requests. `JudgedSessionSupervisor` is the one-shot adapter;
`CoordinatorService` is the long-running HTTP adapter.

Both take a worker's boundary from operator-owned configuration and send it
explicitly on `thread/start`. `WorkerPermissions` accepts `on-request` or
`untrusted` as the approval policy, the `user` reviewer, and `read-only` or
`workspace-write` as the sandbox mode. `never` is rejected because it would
execute unjudged. The protocol's `AskForApproval` enum lists `untrusted` and
Codex CLI 0.154.0 accepts it as a `thread/start` parameter, though the same CLI
removed it as a config value — one more reason these values travel on the wire
rather than sitting in a file.

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
The turn sandbox derived from `sandbox_mode` is unchanged either way and still
disables network access. Worker prompts are never the mechanism. A managed
thread is then registered with an immutable session ID, canonical project root,
and `ApprovalPolicy`, and `session.started` records the source and digest of the
permissions the session was derived from, so an audit can compare one session's
boundary against the next.

Every `turn/start`, including follow-up turns, carries the sandbox policy captured
at registration. Read-only workers get a network-disabled read-only policy.
Workspace-write workers get:

- the canonical project as their only writable root;
- network disabled;
- the ambient `$TMPDIR` write exception disabled; and
- the `/tmp` write exception disabled.

The app-server's OS sandbox is the execution-time boundary, so the restriction also
applies to subprocesses, standard temporary-file APIs, symlinks, and other indirect
effects. No worker-authored file feeds this boundary, and a registered
session replays the boundary it started with.

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
one-shot judge path is treated like any other judge failure and denies the request.

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

The one-shot adapter calls a `Judge` directly. In service-owned mode, the live
broker also invokes an independent restricted judge and rejects HTTP verdicts;
explicit external mode instead accepts verdicts over HTTP. Both modes emit a
normalized `approval.requested` event.

## Deciding a case by code

Between normalization and the judge sits `ApprovalPolicy.decide_by_code`. Both
adapters call it, so the live broker and the one-shot handler decide
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
before this step: the turn sandbox makes the project the only writable root with
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
never amended by this path. Both adapters share the step; the one-shot handler
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

## Two-tier constitution

`Constitution` holds one overall `PolicyDocument` and a mapping of canonical
project directories to their own documents. `for_project` selects the most
specific entry at or above a project; `trusted_policy` returns the overall
document plus that one project document and nothing else, so a judge for one
project cannot read another project's policy. Service-mode configuration always
sets `require_project_policy`, so an uncovered project raises
`PolicyUnavailable`, which the judge converts to a denial without calling a
model, and `start_session` refuses the project outright. The flag exists because
the one-shot path builds a one-tier `Constitution.single` from judge-policy text
and has no per-project documents to require. `provenance` returns the source path and text
digest of each tier for the audit record. Tier selection is keyed on the
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
approval fields are untrusted evidence. The one-shot judge prompt separates that
evidence from trusted instructions, but prompt construction is not the security
boundary. Decisions bind to normalized fields and registered identity.

Audit events record `declaredIntent` separately from `enforcedCapabilities`. The
former may contain worker-authored prose; the latter is derived solely from trusted
registration and runtime sandbox policy. A third field, `policy`, records which
constitution tiers the judge was given by source and digest; it is derived from
trusted configuration and is absent in external mode, where the service runs no
judge. The judge prompt carries the normalized `declared_intent` alongside the
raw request so the two constitutions are applied to what the coordinator
actually parsed, not only to the worker's own wording.

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
