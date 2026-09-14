# Assessment: prompt injection against the judge in a multi-capability workspace

This is an analysis of the current code (`src/codex_coordinator/coordinator.py`,
`service.py`, `config.py`), not a live experiment; every finding is stated so it
can be falsified by a test. Findings 1-4 are tracked as issues under
[issue tracking](issue-tracking.md); findings 5-7 are recorded here as
accepted-risk notes for the trusted local prototype. The milestone that closes
findings 1-4 is M3 in the [roadmap](ROADMAP.md).

## Scenario

One operator, one machine, a controlled network, and the supported
loopback-only service in `approval_mode = "service"`. Three worker projects are
registered as allowed roots and driven by one coordinating Codex session:

- **Project MAIL** — reads and drafts the operator's Gmail.
- **Project SELF** — makes coding changes to its own source tree.
- **Project WEB** — searches the web and summarizes results.

MAIL and WEB both ingest text written by people who are not the operator. SELF
writes code that later runs. This is the first configuration in which the
coordinator's untrusted-evidence boundary is load-bearing for something other
than a demo: the injected text is no longer hypothetical, it arrives on every
turn, and two of the three workers cannot do their job without it.

The question this assessment asks is narrow: **can text that a worker ingests
change what the judge approves?** Not "can it fool the worker" — it can, and
that is assumed — but whether the deterministic ceiling still holds when the
worker is an obedient relay for an attacker's instructions.

## What holds

These are worth stating because they bound the findings below.

- The judge prompt is built with `json.dumps` and the judge answers under an
  `--output-schema` (`coordinator.py:466-506`). Injected text cannot break the
  prompt's framing or add fields to the verdict.
- `ApprovalPolicy.constrain` (`coordinator.py:403-431`) re-derives every limit
  from trusted registration: a judge cannot widen permissions past the
  request or the operator ceiling, add permissions to a command or file
  approval, grant session scope when `allow_session_approval` is false, or pick
  a decision the request did not offer.
- `normalize_path` (`coordinator.py:352-362`) rejects every path outside the
  registered project, so cross-project writes fail before the judge is asked.
- Judge timeout, exception, malformed response, and oversized evidence all deny
  (`coordinator.py:483-506`, `service.py:268-273`, `service.py:300-316`).
- Command approvals never return an execpolicy or network-policy amendment:
  `_encode` emits only `accept`/`acceptForSession` (`coordinator.py:588-598`),
  so a worker's `proposedNetworkPolicyAmendments` cannot be granted.

So injection cannot make the judge *exceed* the ceiling. Every finding below is
a case where the damaging action is **inside** the ceiling.

## Findings tracked as issues

1. The network ceiling is one bit, and any network-capable worker needs it —
   #0001.
2. The judge is never told what the worker was asked to do — #0002.
3. One constitution governs three incompatible capability profiles — #0003.
4. A self-modifying worker can rewrite the coordinator that judges it — #0004.

Injection cannot make the judge *exceed* the ceiling. Each finding above is a
case where the damaging action is **inside** the ceiling.

## Accepted-risk notes

### 5. Worker-written files are re-ingested with no integrity mark

A file change inside the project is the normal, correct thing for the judge to
approve. When the written file is `AGENTS.md`, a test fixture, a prompt
template, or a script the worker later runs, the approval is also a durable
edit to the worker's own future instructions. The judge sees one in-scope diff
and cannot see that the target feeds back into context.

This is the persistence step that turns a single injected email into a standing
behavior change. It is listed below the first four because the existing
per-turn boundary still holds — each later action is judged on its own — but the
judge is by then reading a case shaped by an earlier attacker-authored file.

**Acceptance criteria**

- Approval evidence flags when a file change targets a path known to influence
  later turns (agent instruction files, `.codex/`), and the constitution
  addresses that class explicitly.
- A regression covers a worker writing its own `AGENTS.md` and shows the flag in
  `approval.requested`.

### 6. Whatever else Codex honors from a worker-writable project config

The coordinator no longer reads anything from inside a worker project. A
project's approval policy, reviewer, and sandbox mode come from an
operator-owned permissions file beside `operator.toml`, validated at startup
and sent on `thread/start`, and unknown keys in that file are rejected rather
than ignored.

What that does not settle is what Codex CLI 0.154.0 itself honors from a
`<cwd>/.codex/config.toml` when a thread runs with a worker project as its
`cwd`. The three keys that decide the governance boundary are sent explicitly on
the wire and the turn sandbox is sent on every `turn/start`, so those are not
reachable from such a file; anything else the runtime reads from it is
unenumerated.

**Acceptance criteria**

- The set of keys Codex honors from a project config under an app-server thread
  is enumerated in `docs/security.md` with a stated reason each is harmless
  under the explicit `approvalPolicy` and `sandboxPolicy` the coordinator sends,
  or a worker project carrying such a file is rejected at session start.
- A live test starts a session for a project holding a `.codex/config.toml` that
  contradicts the operator declaration and shows which side the runtime used.

### 7. The judge's `reason` is a channel into the coordinator

`resolve` emits the judge's reason verbatim in `approval.resolved`
(`service.py:372-383`), and the coordinating session reads the event stream.
The reason is written by a model whose input was attacker-controlled, and it
arrives at the coordinator carrying the authority of the judge. It is not sent
back to the worker — `_encode` drops it (`coordinator.py:587-598`) — so this is
one-directional, coordinator-ward laundering.

Combined with finding 2, this is the cross-project path: text ingested by MAIL
reaches the coordinator as either untrusted evidence or a judge reason, and the
coordinator then composes the prompt that starts SELF's next turn. SELF's
subsequent requests are in-scope, well-motivated, and approved.

**Acceptance criteria**

- Judge reasons are length-bounded and marked as model-authored wherever the
  coordinator can read them.
- The event schema distinguishes trusted coordinator-authored text from
  model-authored text in `approval.resolved`.

## Evidence limits of this assessment

The offline suite cannot demonstrate any of this. Confirming findings 1, 2, and
5 requires the opt-in live E2E with a real ingestion source and real injected
text; finding 4 requires a deliberate self-modification run against a scratch
copy of the tree, not this checkout. Do not mark any criterion above satisfied
on the strength of the fast suite alone.
