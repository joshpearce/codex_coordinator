# Coordinator E2E fixture

This is the starting project for the top-level coordinating Codex session. Each live
run copies it beside the two child projects. The coordinator makes control-plane
requests over HTTP; child Codex sessions author application changes in their own
workspace-write sandboxes. The coordinator profile can inspect sibling outputs but
cannot write them directly.
