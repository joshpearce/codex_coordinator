# Live compatibility evidence

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
