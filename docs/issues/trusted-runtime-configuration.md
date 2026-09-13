# Externalize trusted runtime configuration

## Status

Complete — child of [the generic coordination goal](generic-coordination-goal.md).
Operator TOML/environment/CLI precedence, root authorization, ceilings,
socket and worker settings, and preflight validation are implemented.
The one-shot CLI now also requires an operator-approved root and rejects
symlink escapes before it reads project-local configuration.
Worker config symlinks are resolved before reading and rejected when they leave
the canonical worker project; in-project links remain supported.
The one-shot CLI propagates trusted judge policy and timeout; the reusable API
accepts a caller-supplied judge implementation and the service supports an
external decision actor. The parent live gate and security child's judge
read-isolation requirement remain pending; this status closes configuration,
not those separate verification obligations.

## Problem

The one-shot command fixes the daemon socket location and takes its policy as
positional text. The service exposes only a few command-line switches and
accepts any project path with a compatible `.codex/config.toml`. Although
`ApprovalPolicy` supports trusted permission ceilings, service-created policies
do not receive them. This prevents an operator from reusing the service with
explicit project and approval policy boundaries.

## Desired behavior

Define one operator-owned configuration surface for the Codex executable and
connection, approved project roots, worker and judge settings, session-approval
choice, and per-project permission ceilings. Apply it consistently to the
one-shot and long-running paths where each setting is relevant. Project-local
worker configuration remains validated evidence, not a source of additional
coordinator authority.

## Acceptance criteria

- Config values have a documented schema, safe defaults, validation errors,
  and precedence between file, environment, and CLI inputs.
- Canonical project paths must be within configured allowed roots before any
  project configuration is read or a thread is started; symlink escapes fail.
- The service passes the applicable trusted permission ceiling into each
  `ApprovalPolicy`. Tests show narrowing succeeds and widening beyond either
  the original request or trusted ceiling fails closed.
- The Codex executable/socket, worker model and reasoning effort, judge
  implementation/settings, and approval lifetime policy are configurable
  without editing package source or example templates.
- No worker-authored prompt, project file, event, or judge output can change
  trusted configuration after registration.

See also [arbitrary project paths](arbitrary-project-paths.md).
