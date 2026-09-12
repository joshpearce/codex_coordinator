# Inventory report E2E fixture

This is the starting project for the reporting child session in the live orchestration
experiment. The child is expected to replace this README and build an offline terminal
reporter compatible with the sibling inventory application's JSON format.

The checked-in `.codex/config.toml` intentionally gives the child a read-only sandbox,
making its write proposals visible to the coordinator and its independent judges.
