# Inventory report E2E fixture

This is the starting project for the reporting child session in the live orchestration
experiment. The child is expected to replace this README and build an offline terminal
reporter compatible with the sibling inventory application's JSON format.

The checked-in `.codex/config.toml` gives the child a workspace-write sandbox. The
coordinator service owns the session and sends its prompts, while the child authors
the application directly inside this project.
