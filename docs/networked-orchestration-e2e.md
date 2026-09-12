# Agent-driven orchestration E2E

This opt-in experiment makes the deliberately recursive architecture concrete:

```text
live_e2e harness
  └─ coordinating `codex exec`
       ├─ starts `codex-coordinator-service` with no process timeout
       │    └─ owns app-server WebSocket and child threads
       ├─ scans the service stdout JSONL file
       ├─ calls the loopback HTTP API with curl
       └─ for each approval, starts a fresh `codex exec` judge
```

The two child sessions build compatible inventory applications in separate temporary
projects copied from [`examples/inventory-app`](../examples/inventory-app) and
[`examples/inventory-report`](../examples/inventory-report). Their checked-in
`.codex/config.toml` files make the app-server sandbox read-only, so application edits exercise the
approval path. One child is also instructed to attempt a network operation, which the
[constitution](../examples/coordinator/constitution.md) requires the independent judge to deny. The
coordinator must observe at least one `approve_once` and one `deny`, reconcile the two
applications, run their tests, write `result.json`, and shut down the service.

The top-level session runs from the checked-in
[`examples/coordinator`](../examples/coordinator) template. A live workspace has three
sibling projects, and all coordinator artifacts—including the resolved `goal.md`, child
goal prompts, constitution, event stream, decisions, and result—live under
`workspace/coordinator/`. Its `AGENTS.md` explicitly documents that application writes
must be delegated over HTTP, though the broad POC sandbox means this is not enforced by
the operating system.

Run it only when real Codex usage and a broad sandbox for the top-level coordinator
are acceptable:

```sh
uv run codex-coordinator-live-e2e --workspace /tmp/codex-orchestration-run
```

The top-level coordinator uses `--dangerously-bypass-approvals-and-sandbox` because
it must start a local server, reach the daemon socket, invoke nested Codex judges, and
write both disposable projects without a human approval channel. The children and
judges retain their narrower configurations. This separation demonstrates the
mechanism; it is not a production security boundary.

The harness defaults the coordinating session to `gpt-5.6-sol` with medium reasoning.
Child workers and per-approval judges remain on `gpt-5.6-luna` with low reasoning to
keep the repeated work quick. Each role has separate `--*-model` and
`--*-reasoning-effort` options.

The service has no approval timeout. If the coordinating session crashes, an approval
can remain pending until the service is stopped. The explicit workspace preserves the
goal prompt, stdout events, final response, child projects, and summary for debugging.

The coordinating session starts the service as a foreground command held by its shell
execution session. It intentionally does not daemonize with `nohup`: managed execution
hosts may reap detached descendants as soon as the launching tool call returns.

The ordinary `pytest` suite does not consume Codex usage. It deterministically covers
the same critical plumbing: concurrent RPC multiplexing, stdout approval emission,
HTTP verdict submission, and child-session creation.
