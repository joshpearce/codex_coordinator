# One-shot judges may read overly broad local data

## Priority

Medium — scope and isolation.

## Problem

A read-only sandbox prevents mutation but may still expose files outside the child
project. A judge could inspect unrelated repository or user data while evaluating
an approval. Structured output constrains the response format, not what the judge
can read or incorporate into its reasoning.

## Desired behavior

Judges should receive a minimal evidence bundle and have filesystem reads restricted
to only the constitution, normalized request, and explicitly necessary project
metadata.

## Acceptance criteria

- The judge runs with an explicit minimal read allowlist.
- Secrets, sibling projects, and unrelated user files are unavailable.
- The verdict record identifies the evidence provided to the judge.
- A test proves an attempted unrelated read is denied.
