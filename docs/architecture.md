# Architecture

The repository contains two adapters over one governance boundary and the
app-server transport.

## Session creation and execution

`ProtocolClient` owns JSON-RPC framing and multiplexes responses, notifications,
and server approval requests. `JudgedSessionSupervisor` is the one-shot adapter;
`CoordinatorService` is the long-running HTTP adapter.

Both read the worker project's `.codex/config.toml` once at registration, accept
only `on-request`, the `user` reviewer, and `read-only` or `workspace-write`, and
send those values explicitly on `thread/start`. A managed thread is then registered
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

Permission objects have a closed schema. Requested values must fit an immutable,
trusted allowlist, and a judge-proposed permission response must be a subset of both
the normalized request and that allowlist. Commands asking for additional sandbox,
network, or persistent policy authority are denied because a plain command response
cannot safely narrow those capabilities.

Session-wide approval is off by default. It is available only when trusted service
startup or embedding code enables it and the individual command request offers
`acceptForSession`. A judge can always deny or narrow a request, but cannot expand
its permission or lifetime ceiling.

The one-shot adapter calls a `Judge` directly. The live broker emits a normalized
`approval.requested` event and accepts a verdict over HTTP. In both cases, invalid
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
registration and runtime sandbox policy.

## Deliberate remaining scope

The service still binds to loopback by default and has no HTTP authentication,
durable state, approval timeout, or event-retention limit. Those medium, low, and
deferred issues remain separately tracked; they do not weaken request normalization
or the execution boundary described here.

Most tests use fake clients and local loopback HTTP. One regression starts a real
local `codex app-server --stdio` process without model inference and proves that a
benign-looking unit-test command cannot write outside its project through a standard
temporary-file API, subprocess, or symlink.
