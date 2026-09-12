# Approval judges cannot see transitive command side effects

## Priority

High. A project session can obtain approval for a benign-looking command whose
runtime effects exceed what the governance decision evaluated.

## Status

Deferred.

## Problem

An app-server approval request can identify a command, its working directory, and its
declared intent without enumerating every filesystem or process effect that command
will cause. In the live E2E, the constitutional judge approved
`python -m unittest discover -v` as a project-local operation, but the generated tests
used `tempfile` and therefore wrote outside the child project.

The judge followed the visible command-level evidence, yet the result violated the
constitution's stricter requirement that every filesystem effect stay inside the
child project. This is a general time-of-check versus execution-effects gap, not a
special case limited to Python tests.

## Desired behavior

Approval should be based on enforceable runtime boundaries or on effects measured by
the execution layer, rather than relying on an LLM to infer all transitive effects
from a command string.

## Possible approaches

- Treat the sandbox as the authoritative filesystem boundary and phrase the
  constitution in terms of allowed sandbox capabilities rather than inferred effects.
- Attach a more precise execution manifest to approval requests when the command can
  provide one.
- Run approval candidates in a tracing or dry-run environment and judge the observed
  accesses before allowing the real execution.
- Give tests a project-local temporary directory through `TMPDIR` and enforce that
  environment in the execution policy.

## Acceptance criteria

- A command approved as project-local cannot write outside its permitted roots.
- The decision record distinguishes declared intent from enforced capabilities.
- Regression coverage includes a seemingly harmless test command that calls a
  standard temporary-file API.
