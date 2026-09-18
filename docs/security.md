# Security and trust model

Codex Coordinator is a trusted, single-user, loopback-only prototype. It is not an authorization service and must not be presented as safe for remote, multi-user, untrusted-client, or production use.

The operator trusts the host/user Codex configuration and every configured project's local Codex configuration as-is. Either may grant unrestricted filesystem, process, or network access. The coordinator neither validates nor narrows those capabilities. A valid approval request from a managed child is accepted automatically, using `acceptForSession` when offered and `accept` otherwise; permission requests receive the requested permission payload.

Configuration prevents request-time arbitrary paths by exposing stable project names, but this is operability and input validation, not sandboxing. Paths may overlap or contain the coordinator itself.

The control plane remains loopback-only with browser Origin/Host rejection and bounded HTTP parsing. It has no authentication; any local process able to reach it may control sessions and read events. Do not publish it through a reverse proxy. Issues #0008 and #0009 track hardening required before broader deployment.

Events may contain prompts, model output, local paths, commands, and diffs. Default stdout is metadata-only and known secret-shaped fields are redacted, but retention and redirected-output handling remain operator responsibilities (#0010). In-memory events are bounded (#0011), and HTTP input/concurrency limits remain enforced (#0012).
