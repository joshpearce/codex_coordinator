# Coordinator E2E fixture

This is the starting project for the top-level coordinating Codex session. Each live
run copies it beside the operator directory and two child projects. The operator
directory holds `operator.toml` for service settings, one permissions file per
child declaring its execution boundary, the overall
`constitution.md`, and one project constitution per child for the service-owned
independent judge; none of it is writable by this coordinating session, and the
coordinator cannot choose which project constitution applies to a project.
The coordinator makes control-plane requests over HTTP; child Codex sessions
author application changes in their own workspace-write sandboxes. The children
are not told that any of this exists: their prompts are plain implementation
tasks, and what reaches a judge is decided by each child's operator-owned
permissions file, which that child cannot read or write. The coordinator profile can inspect sibling outputs but
cannot write them directly.
