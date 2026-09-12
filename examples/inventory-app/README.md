# Inventory app E2E fixture

This is the starting project for the inventory-domain child session in the live
orchestration experiment. The child is expected to replace this README and build the
application, persistence format, CLI, and tests.

The checked-in `.codex/config.toml` gives the child a workspace-write sandbox. The
coordinator service owns the session and sends its prompts, while the child authors
the application directly inside this project.
