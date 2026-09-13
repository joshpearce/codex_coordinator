# Make the CLI and example portable

## Status

Complete — child of [the generic coordination goal](generic-coordination-goal.md).
The preflight entry point, configurable commands, source-only generic example,
installed two-project fixture, and strict live outcome gate are implemented.
The installed gate and a live generic Codex run passed on an unrestricted
operator host. The live run used two unrelated disposable projects and
recorded two approval requests, an exact accepted command, an exact declined
command, two app-server resolution receipts, terminal worker turns, and a
completed follow-up. The approved marker was created; the denied marker was
not. The gate's exact command strings matched the app-server's on-wire shell
wrapper, rather than only the inner `exec_command` text.
The strict example now requires distinct, non-nested projects and approval
events from both worker sessions; its interactive judge prints normalized
request and capability evidence before asking the operator. Its outcome gate
also requires a correlated `approval.wire_sent` event, so a policy decision
that was never sent cannot count as an approval or denial. It now also requires
the app-server's `serverRequest/resolved` event and a matching terminal command
item status to distinguish receipt/clearance from the command's actual outcome.

## Problem

The current command is a one-shot judged-worker smoke test. The long-running
service needs a separate coordinator to drive its HTTP API, and the only full
workflow example copies inventory-specific projects and goals from this
checkout. That obscures what an installed user must configure and run.

## Desired behavior

Provide a normal user path for an installed package: discover or select a Codex
connection, validate worker-project prerequisites, start the coordinator, and
run a generic local multi-worker example. Keep the inventory E2E as a test,
not the recommended integration template.

## Acceptance criteria

- CLI help and documentation distinguish the one-shot command, long-running
  service, and reusable library path; required external Codex setup is clear.
- The CLI accepts the relevant trusted connection/configuration inputs rather
  than assuming one home-directory socket or example-specific model.
- A validation command or documented preflight checks installation, Codex
  availability, project configuration, allowed roots, and judge setup before
  starting work; it does not silently weaken unsafe settings.
- A short generic example coordinates two operator-selected projects and
  demonstrates an approval, denial, follow-up, and terminal result without
  importing repository examples or using fixed absolute paths.
- If runnable templates are part of the installed experience, they are
  packaged and accessed as package resources; otherwise the docs explicitly
  keep them as source-only examples.
