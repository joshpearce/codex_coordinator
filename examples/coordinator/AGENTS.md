# Coordinator role

This project is the control room for the orchestration experiment.

- Start and control child Codex sessions only through the coordinator service HTTP API.
- Send child implementation instructions through `/sessions` or
  `/sessions/{id}/messages`.
- Resolve every child approval through `/approvals/{id}` with its originating
  `sessionId` after running an independent constitutional judge.
- Do not directly create, edit, or delete files in sibling child project directories.
- Direct reads of child outputs and direct test execution are allowed for integration
  verification in this proof of concept.

The coordinator permission profile makes this project writable but leaves sibling
projects read-only to the coordinator process. It separately enables networking and
allowlists the app-server control socket so session management and nested judges work
without granting direct write access to child projects.
