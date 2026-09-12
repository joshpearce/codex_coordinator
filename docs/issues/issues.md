# Security issue priorities

This index prioritizes issues according to one rule: **high priority is reserved
for paths by which a project Codex session can influence, bypass, or confuse its
own governance**. Network exposure, authentication, and socket protection are
intentionally deferred for this local proof of concept.

## High priority — governance integrity

1. [Project output can influence the coordinator and judges](project-output-prompt-injection.md)
2. [The live approval broker does not enforce the constitution](live-broker-policy-enforcement.md)
3. [The shared app-server connection accepts unmanaged thread requests](unmanaged-thread-requests.md)
4. [Approval judges cannot see transitive command side effects](approval-side-effect-visibility.md)

## Medium priority — scope and isolation

5. [Session creation accepts arbitrary project paths](arbitrary-project-paths.md)
6. [One-shot judges may read overly broad local data](judge-read-scope.md)
7. [The coordinator has unrestricted network access](coordinator-network-scope.md)

## Low priority — resilience and data handling

8. [Logs retain sensitive orchestration content](sensitive-event-logs.md)
9. [In-memory event and item state is unbounded](unbounded-runtime-state.md)
10. [The HTTP parser has no resource limits](http-resource-limits.md)
11. [Approval requests can remain pending forever](approval-lifecycle.md)

## Deferred — local control-plane hardening

12. [The HTTP control plane has no authentication](control-plane-authentication.md)
13. [Socket and listener exposure is not hardened](socket-listener-protection.md)

The deferred classification is deliberate, not a claim that those issues are
safe in a networked or multi-user deployment. They must be revisited before the
service is exposed beyond a trusted, single-user proof-of-concept environment.
