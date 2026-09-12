# Coordinator E2E fixture

This is the starting project for the top-level coordinating Codex session. Each live
run copies it beside the two child projects. The coordinator makes control-plane
requests over HTTP; child Codex sessions author and apply application changes after
approval. This separation is instructional in the POC, not enforced by permissions.
