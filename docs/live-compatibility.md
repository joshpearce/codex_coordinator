# Live compatibility evidence

On 2026-09-24, preflight was run from the managed Codex parent against the
host user's standard socket with the coordinator checkout configured as a
project. The parent lacked metadata access to the socket target. The command
exited 2 with one actionable line naming the socket and underlying
`Operation not permitted` reason, with no traceback and no suggestion to start
another daemon. Command (environment value abbreviated here only by using the
repository path already shown in this checkout):

```console
CODEX_COORDINATOR_PROJECTS='{"coordinator":"/Users/josh/code/codex_coordinator"}' \
  uv run codex-coordinator-preflight --require-socket
```

This denial is expected evidence for the managed parent boundary, not a claim
that the app-server was unavailable.

Also on 2026-09-24, the installed `0.1.0` service was launched from the
coordination-workspace template on port 8876 under the per-user launchd label
`com.openai.codex-coordinator.501.8876`. The initiating `make start` tool call
returned only after `/health` succeeded. A later, separate tool call received
the same service ID from `/health`, listed sessions, and reported healthy
status. A final `make stop` unloaded the supervisor and completed through the
service's SIGTERM shutdown path. No foreground tool cell was retained. Two
diagnostic attempts preceded the passing run: the first exposed launchd's
inability to resolve the service executable, and the second exposed its minimal
PATH for resolving `codex`; the launcher now supplies both deterministically.

The same date's live event-projection exercise started a read-only child on
Codex CLI/app-server `0.156.1`. At an early high-volume snapshot the explicit
debug cursor had already reached 83 notifications, including token updates,
message deltas, MCP startup, item starts, and full command results. The normal
feed held seven concise records: service/session startup, two complete child
messages, and three command-completion correlations. The normal client did not
parse a raw notification schema. The run was bounded by cancelling after the
load proof; sequences 18 and 19 recorded `session.cancelling` and the correlated
`session.interrupted` terminal result, and `GET /sessions` agreed. The service
then stopped cleanly under launchd supervision.

Cursor recovery was then exercised with an intentionally tiny four-event
orchestration window. After conservative delay, `GET /events?after=0` returned
410 with oldest sequence 5, latest sequence 8, and an explicit instruction to
reconcile `/sessions` and resume after 8. The session surface already retained
the child's latest complete message. The child completed normally while the
consumer was delayed; `/sessions` reported `completed` with that final message,
and resuming at 8 returned sequences 9 and 10 (`child.message` and
`session.completed`). Together with the default-window high-volume run above,
this proves both normal retention isolation and lossless required-evidence
recovery without aggressive polling.

The live blocking-wait exercise ran two read-only children concurrently. One
`wait=30` request returned correlated progress for both sessions (sequences
4–7), and the next returned both final messages and both `session.completed`
records (sequences 8–11); no shell sleep loop or raw-event filtering was used.
A `wait=0.1` request returned `outcome=timeout`, and an ahead cursor returned
409 with the service generation's latest sequence. The first supervised-stop
trial exposed that SIGTERM bypassed coordinator shutdown, and the second showed
an open waiter could lose its response during teardown. After adding signal
handling plus HTTP-handler draining, the repeated open `wait=30` request
returned sequence 2 `service.shutting_down` with `outcome=shutdown` and exit 0
while `make stop` completed. The failed trials are retained here because they
diagnosed lifecycle defects rather than being hidden or used to weaken tests.

Restart continuity was verified live on `0.156.1` with the launchd workspace
launcher and its mode-0600 registry. Session
`16907814c6c44928b35e7b059179af48` / thread
`01a0d30c-ef4b-7323-8309-e4c75dcf5290` completed, survived a real stop/start
with the same identities and last message, and accepted multiple same-thread
follow-ups whose terminal notifications retained both IDs. A second
approval-enabled fixture session,
`28f92b54fd794002b5e028e2059f198a` / thread
`01a0d30e-b32c-77d1-927c-9d54d4b32650`, survived another real restart. Its
post-restart shell-network follow-up emitted correlated
`approval.auto_approved`, `approval.wire_sent`, `approval.server_resolved`,
command completion, child message, and `session.completed` events (sequences
12–17). A final follow-up was cancelled; one blocking wait returned
`session.turn_started`, `session.cancelling`, and `session.interrupted`
(sequences 18–20). The temporary marker was removed by the child, and the local
test registry was removed after evidence capture.

The final integrated gate combined the six issue behaviors in one bounded
chain on port 8881 with a deliberately tiny four-event window. The externally
supervised launcher remained healthy in a later tool call. Inventory session
`746c81b37a31484888d7bf802308b526` / thread
`01a0d311-4ec1-7f61-a25c-a7f3a4693b90` completed via a blocking event wait,
survived a real stop/start with the same identities and final-message evidence,
and accepted a same-thread follow-up. Normal-feed load expired cursor 2; the
410 response directed reconciliation through `/sessions` and resumption after
7, which returned a clean timeout rather than a replay. A further same-thread
turn was cancelled, and the next blocking request returned
`session.turn_started`, `session.cancelling`, and `session.interrupted`
(sequences 8–10). The run used no shell sleep polling, never fetched or filtered
the raw debug feed, and shut down through the supervised orderly path. Automatic
approval continuity is established by the immediately preceding recovered
fixture run (sequences 12–17); the integrated follow-up's first network command
completed with status 128 and did not issue a server approval request, so it is
not misreported as approval evidence. App-server token-usage notifications are
intentionally redacted from retained evidence, so a reliable live token total
was not available.

On 2026-09-23, the opt-in harness passed against Codex CLI and app-server
`0.156.1`. This version exposes the standard control socket through an
owner-controlled symlink; the coordinator validated both the link and its
private, user-owned Unix socket target before connecting.

The run started `inventory-app` and `inventory-report` concurrently. Both
inherited the host model `gpt-5.6-sol` and their distinct project-local
reasoning efforts, `medium` and `high`, and both turns completed.

The workspace-write `inventory-app` child requested approval before writing a
marker outside its project. The coordinator emitted `approval.auto_approved`,
sent the response, observed `serverRequest/resolved`, and then observed the
command complete. The intentionally unrestricted `inventory-report` child
created and verified a fresh marker at
`/tmp/codex-coordinator-live-unrestricted-0.156.1-20260923-run3.txt`, showing
that the coordinator did not narrow its inherited project configuration.

The final summary was:

```json
{
  "autoApprovals": 1,
  "effectiveModels": ["gpt-5.6-sol", "gpt-5.6-sol"],
  "effectiveReasoningEfforts": ["medium", "high"],
  "states": ["completed", "completed"]
}
```

On 2026-09-18, the opt-in harness ran against Codex CLI 0.154.0 and the host user's already-running app-server.

The run started `inventory-app` and `inventory-report` concurrently without a model or reasoning override. The app-server reported the host model `gpt-5.6-sol` for both threads and the distinct project-local reasoning efforts `medium` and `high`. Both turns completed.

`inventory-app` inherited `approval_policy = "on-request"` and `approvals_reviewer = "user"`. Its network command emitted `item/commandExecution/requestApproval`; the coordinator emitted `approval.auto_approved`, sent the response, and observed `serverRequest/resolved` before the command completed.

`inventory-report` inherited its intentionally unrestricted configuration and created a fresh marker outside its project at `/tmp/codex-coordinator-live-unrestricted-final-20260918.txt`. The harness verified the exact marker content in the same run, demonstrating that the coordinator did not narrow the child.

The final summary was:

```json
{
  "autoApprovals": 1,
  "effectiveModels": ["gpt-5.6-sol", "gpt-5.6-sol"],
  "effectiveReasoningEfforts": ["medium", "high"],
  "states": ["completed", "completed"]
}
```

This is environment-specific compatibility evidence, not a claim that the service is an authorization boundary or safe outside its trusted, single-user, loopback-only scope.
