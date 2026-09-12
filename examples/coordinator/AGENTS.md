# Coordinator role

This project is the control room for the orchestration experiment.

- Start and control child Codex sessions only through the coordinator service HTTP API.
- Send child implementation instructions through `/sessions` or
  `/sessions/{id}/messages`.
- Resolve every child approval through `/approvals/{id}` after running an independent
  constitutional judge.
- Do not directly create, edit, or delete files in sibling child project directories.
- Direct reads of child outputs and direct test execution are allowed for integration
  verification in this proof of concept.

The final restriction is prompt-level, not an operating-system boundary. This
coordinator uses a broad sandbox so it can access localhost, the daemon socket, and
nested Codex processes; therefore it is technically capable of editing sibling files.
