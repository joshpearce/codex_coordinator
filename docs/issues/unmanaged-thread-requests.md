# The shared app-server connection accepts unmanaged thread requests

## Priority

High — governance integrity.

## Problem

The live `ApprovalBroker` creates an approval for every server request received on
the shared app-server connection, even when its thread ID is not registered to a
coordinator-managed session. Notifications from unrelated threads are also logged.
This can mix unrelated session data into the coordinator's evidence and can let a
request from the wrong thread receive a verdict intended for a governed worker.

## Desired behavior

Only explicitly registered worker threads should participate in approval handling
or session event reporting.

## Acceptance criteria

- Requests from unknown or missing thread IDs are denied immediately and are never
  placed in the pending approval queue.
- Notifications are emitted only for managed threads, apart from a minimal,
  non-sensitive diagnostic counter if needed.
- Approval IDs remain bound to the originating managed thread and session.
- Tests cover concurrent managed and unmanaged threads.
