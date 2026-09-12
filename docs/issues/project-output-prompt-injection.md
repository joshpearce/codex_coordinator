# Project output can influence the coordinator and judges

## Priority

High — governance integrity.

## Problem

Project sessions control messages, command descriptions, diffs, and files that the
coordinator reads while deciding what to do next. A project can place instructions
in those channels that persuade the coordinator to weaken policy, mischaracterize
an approval, disclose data, or send an unsafe verdict. Encoding approval data as
JSON and labeling it untrusted reduces ambiguity but does not create an isolation
boundary.

## Desired behavior

Project-controlled content must be treated as evidence, never as governance
instructions. A deterministic layer must limit every approval and coordinator
action regardless of model interpretation.

## Acceptance criteria

- Child-authored text cannot modify the constitution or permission ceiling.
- Approval decisions bind to normalized request fields rather than prose summaries.
- Adversarial child messages and files are covered by end-to-end tests.
- Ambiguous or conflicting evidence fails closed.
