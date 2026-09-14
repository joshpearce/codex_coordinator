# Architecture

The repository contains two adapters over one governance boundary and the
app-server transport.

## Session creation and execution

`ProtocolClient` owns JSON-RPC framing and multiplexes responses, notifications,
and server approval requests. `JudgedSessionSupervisor` is the one-shot adapter;
`CoordinatorService` is the long-running HTTP adapter.

Both read the worker project's `.codex/config.toml` once at registration, accept
only `on-request`, the `user` reviewer, and `read-only` or `workspace-write`, and
send those values explicitly on `thread/start`. `never` is rejected because it
would execute unjudged. The protocol's `AskForApproval` enum also lists
`untrusted`, but Codex CLI 0.154.0 removed it as a config value and fails
`thread/start` with "no longer supported", so it is rejected at startup rather
than deferred into an opaque runtime error.

How much reaches a judge is operator-owned. `worker_approval_policy` in
`operator.toml` accepts `on-request` or `untrusted` and is sent as the
`thread/start` `approvalPolicy` parameter, overriding the project's declared
value. The same CLI that rejects `untrusted` in a project config accepts it on
the wire, and sending it keeps the setting outside the worker's own writable
root. Under `untrusted` the runtime raises an approval request before anything
it does not already trust; under `on-request` a worker that never asks is never
judged. The turn sandbox derived from `sandbox_mode` is unchanged either way and
still disables network access. Worker prompts are never the mechanism. A managed thread is then registered
with an immutable session ID, canonical project root, and `ApprovalPolicy`.

Every `turn/start`, including follow-up turns, carries the sandbox policy captured
at registration. Read-only workers get a network-disabled read-only policy.
Workspace-write workers get:

- the canonical project as their only writable root;
- network disabled;
- the ambient `$TMPDIR` write exception disabled; and
- the `/tmp` write exception disabled.

The app-server's OS sandbox is the execution-time boundary, so the restriction also
applies to subprocesses, standard temporary-file APIs, symlinks, and other indirect
effects. Project-controlled config changes cannot expand a registered session later.

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
