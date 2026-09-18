# Architecture

The coordinator is a thin multiplexer over one connection to the host user's already-running Codex app-server.

`OperatorConfig` maps stable project names to absolute directories and carries only orchestration settings. `CoordinatorService` resolves a requested name, starts a thread with that path as `cwd`, starts turns, correlates notifications, and maintains bounded session/event state. `ProtocolClient` owns the single reader and correlates concurrent calls and server requests. `AutomaticApprovalHandler` recognizes the approval methods supported by the pinned compatibility gate and immediately returns the protocol-defined acceptance response.

Thread startup sends only `cwd` and an optional model override. Turn startup sends only the thread identity, prompt, and an optional reasoning-effort override. The coordinator does not send an approval policy, reviewer, permission profile, sandbox, rules, workspace roots, or other security setting.

The service does not start or manage the app-server and does not set `CODEX_HOME`. Codex therefore resolves host and project configuration as it would for a session started naturally at the configured path.

This design deliberately has no coordinator-owned governance layer. Project registration is authority to start there; effective child capabilities come from Codex configuration outside this package.
