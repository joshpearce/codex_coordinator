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
