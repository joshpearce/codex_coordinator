# Issue index

## Reusable coordinator roadmap

[Goal: installable coordinator for generic local workflows](generic-coordination-goal.md)
tracks six implementation issues:

1. [Expose a reusable coordination API](generic-coordination-api.md)
2. [Externalize trusted runtime configuration](trusted-runtime-configuration.md)
3. [Make the CLI and example portable](portable-cli-and-example.md)
4. [Define session, approval, and event lifecycles](session-lifecycle-and-events.md)
5. [Enforce the supported security boundary](generic-coordination-security.md)
6. [Verify the installed artifact and Codex compatibility](installed-artifact-and-compatibility.md)

## Resolved: agent-driven coordination approval governance

[Make the service own constitutional approval judging](service-owned-constitutional-judge.md)
implements mandatory independent review of worker approvals without asking the
coordinating Codex session to post its own approving verdicts. The service
applies the deterministic verdict constraints after independent judging.

## Security issue priorities

The security list below prioritizes issues according to one rule: **high priority is reserved
for paths by which a project Codex session can influence, bypass, or confuse its
own governance**. Network exposure, authentication, and socket protection are
intentionally deferred for this local proof of concept.

## High priority — governance integrity

1. [Project output can influence the coordinator and judges](project-output-prompt-injection.md)
2. [The live approval broker does not enforce the constitution](live-broker-policy-enforcement.md)
3. [The shared app-server connection accepts unmanaged thread requests](unmanaged-thread-requests.md)
4. [Approval judges cannot see transitive command side effects](approval-side-effect-visibility.md)
5. [Command approvals ignore the request's offered decisions](unoffered-command-approval.md)
6. [Mutable command evidence can enable session-wide approval](session-approval-evidence-mutation.md)
7. [Mutable permission evidence can widen request scope](permission-request-evidence-mutation.md)

## Medium priority — scope and isolation

8. [Session creation accepts arbitrary project paths](arbitrary-project-paths.md)
9. [One-shot judges may read overly broad local data](judge-read-scope.md)
10. [The coordinator has unrestricted network access](coordinator-network-scope.md)

## Low priority — resilience and data handling

11. [Logs retain sensitive orchestration content](sensitive-event-logs.md)
12. [In-memory event and item state is unbounded](unbounded-runtime-state.md)
13. [The HTTP parser has no resource limits](http-resource-limits.md)
14. [Approval requests can remain pending forever](approval-lifecycle.md)

## Deferred — local control-plane hardening

15. [The HTTP control plane has no authentication](control-plane-authentication.md)
16. [Socket and listener exposure is not hardened](socket-listener-protection.md)

The deferred classification is deliberate, not a claim that those issues are
safe in a networked or multi-user deployment. They must be revisited before the
service is exposed beyond a trusted, single-user proof-of-concept environment.
