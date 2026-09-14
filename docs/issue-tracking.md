# Issue tracking — one file per issue, state in the filename

Defects and follow-up obligations live in top-level `issues/`, one Markdown file
per issue. This is the canonical tracker; do not assume GitHub Issues is the
authoritative backlog for this repository, and do not mirror issues to an
external system without a deliberate ownership decision.

## Filename grammar

`issues/NNNN-<severity>-<state>-<slug>.md`

- `NNNN`: four-digit number, allocated once, never reused or renumbered.
- Severity: `low`, `med`, or `high`.
- State: `new`, `in-progress`, or `closed`.
- Slug: short kebab-case description.

The filename is authoritative for severity and state; do not repeat them in the
body. Rename tracked files with `git mv`. Before an issue's first commit, use an
ordinary rename. Search by number when resolving references, since filenames
change. For example: `rg --files issues | rg '/0002-'`.

## Body format

```markdown
# NNNN — One-line title

**Found:** date, vantage point, and discovery method
**Affects:** owning repository, component, source path
**Depends on:** #NNNN
                         (omit Depends on when unnecessary)

## What happens

Observed behavior with bounded, sanitized evidence. Separate current observations
from hypotheses, historical evidence, and unknowns.

## Why it matters

Operational consequence: governance integrity, data durability, service
availability, security, measurement quality, or operator effort.

## What would close this

Testable conditions, including live verification for behavior the offline suite
cannot demonstrate.
```

Dependencies use stable numbers only (`#0002`), never filenames. `Depends on:`
is authoritative; avoid redundant reverse dependency lists and cycles.

## Severity

Severity in this repository is anchored to one rule: **high is reserved for paths
by which a project or coordinator session can influence, bypass, or confuse its
own governance.**

- **high:** a governance-integrity path, exposed credentials, data loss or
  recovery risk, or silent failure of the approval and judging path.
- **med:** scope and isolation defects, bounded service degradation, incomplete
  coverage with a known signal, or an access/reproducibility problem.
- **low:** resilience and data-handling gaps, documentation inaccuracies, or
  transient anomalies without demonstrated impact; still record what evidence
  would establish or dismiss them.

Severity expresses consequence, not implementation effort. Network exposure,
authentication, and socket protection are deliberately held below high for the
documented trusted, single-user, loopback-only prototype. That classification is
not a claim that they are safe in a networked or multi-user deployment, and it
must be revisited before the service is exposed beyond that environment.

## Work and closure

Follow the working rules in `AGENTS.md`: check the worktree before editing,
preserve the deterministic approval boundary, and run focused tests for changed
behavior plus the full `pytest` suite for cross-cutting changes.

Search open issues and the roadmap before filing. A roadmap capability is not
itself a defect; an observed failure belongs in an issue even when an existing
plan describes its fix. Link that plan and adopt its acceptance conditions
instead of writing a rival plan. Read an issue's state in its filename before
starting it — a number's position in any list does not mean it is still open.

Prioritize governance integrity first, then scope and isolation, then
resilience. Use `docs/ROADMAP.md` for capability sequencing. Do not close an
issue because code was written or a plan exists; the opt-in live E2E starts real
Codex sessions, and the fast offline suite is not proof of live behavior. Before
deletion, document the fix, validation, observation date, and linked source
references in durable Git history.

Delete the issue file as part of the final commit that completes the verified
work; do not retain a `-closed-` issue file beyond that commit. If a duplicate
is discovered, record its disposition and canonical issue number without
claiming a fix shipped, then delete it in the final commit. Do not delete open
issues or unverified evidence to make the backlog appear smaller.
