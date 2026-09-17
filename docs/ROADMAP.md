# Delivery roadmap

The order follows governance integrity first, then scope and isolation, then
resilience. Individual findings live in `issues/` under the conventions in
[issue tracking](issue-tracking.md); this document tracks capabilities rather
than duplicating their closure criteria.

| Milestone | Deliverable | Completion evidence |
|---|---|---|
| M0 — Deterministic approval boundary | Normalized approval cases, `ApprovalPolicy.constrain` re-deriving every limit from trusted registration, fail-closed judging | Judge cannot widen permissions past the request or operator ceiling, grant unoffered decisions, or escape the registered project; timeout, malformed response, and oversized evidence all deny |
| M1 — Reusable local coordinator | Installable package, externalized trusted configuration, portable CLI and example, defined session/approval/event lifecycles, documented security boundary, installed-artifact and Codex-compatibility gates | Wheel installed outside the checkout drives two unrelated worker projects through accepted and declined approvals, follow-ups, and terminal turns; hosted macOS Verify run passed 177 tests plus the installed-wheel gate |
| M2 — Service-owned constitutional judging | `approval_mode = "service"` loads a trusted `constitution.md` and runs independent judges; explicit `external` mode retained with no silent fallback | Focused (131) and full (185) suites pass; live networked gate approved one project-local command and denied one network command without the coordinating session submitting verdicts |
| M3 — Multi-capability workspace | Host-scoped network ceilings (#0001), trusted task provenance for the judge (#0002), two-tier constitution — overall plus per-project (#0003), worker roots disjoint from the coordinator's own runtime (#0004), worker/control-plane transport isolation when local IPC is required (#0023) | Deterministic denial of an off-allowlist host with no judge call; audit shows what the judge was told the task was; a project's judge cannot see another's policy; startup rejects a writable root containing the coordinator package, interpreter prefix, `codex_command`, or `coordinator_root`; a loopback-capable worker can run its local test IPC but cannot reach the coordinator control transport |
| M4 — Scope and isolation | Restricted judge reads (#0006), narrowed coordinator egress (#0007), allowed-root enforcement hardening (#0005), operator-owned worker permission configuration (delivered), in-project file changes decided by code, with judging reserved for what leaves the project (delivered), deterministic handling of mundane project-local commands (delivered: operator-owned execpolicy rules evaluated by the coordinator), refusal of a worker project carrying Codex rules of its own (delivered) | A denied unrelated read is tested under the pinned CLI and under the App Server restricted-read field once it exists in the generated schema; live E2E succeeds with a narrowed network policy and a negative egress test; a worker project holds no Codex configuration and rewriting one inside it changes no later session's boundary; a live run decides the recorded mundane commands by rule, accepts every in-project file change by containment with no judge call, still escalates an out-of-project read, and a project carrying `.codex/rules` is refused a session by the running service |
| M5 — Resilience and data handling | Bounded runtime state (#0011), redaction and retention for event logs (#0010), generation-aware persisted event cursors (#0025), HTTP resource limits (#0012), approval lifecycle guarantees (#0013) | Loopback integration gates run outside the restricted development sandbox; high-volume notification tests show stable memory; file permissions and retention are documented and tested; a restarted service cannot silently hide events behind a cursor from its predecessor |
| M6 — Control-plane hardening | Authentication on every state-changing and sensitive read route (#0009), socket and listener protection for non-local deployment (#0008) | Authentication failure tests cover every protected route; non-loopback binding requires an explicit secure deployment mode; reverse-proxy guidance requires transport security and authenticated identity |

M0, M1, and M2 are complete for the supported single-user local workflow. Their
issue files were deleted on verification; the evidence is in Git history.

Within M4, the file-change split and the project-rules refusal are complete:
inside its project a worker acts by right under the sandbox, a judge decides
what leaves it, and a project containing Codex rules of its own is refused a
session. Both were built around gaps in the pinned Codex CLI rather than around
a permanent design, and both should shrink when the CLI closes those gaps: the
coordinator answers file-change approvals only because no approval policy
separates them from commands (#0018), and refuses whole projects only because
no switch makes the runtime ignore their rules (#0019). The compatibility gate
asserts the approval vocabulary that makes the first one necessary, so a CLI
upgrade re-asks the question instead of inheriting the answer. Their remaining
M4 siblings — restricted judge reads (#0006), narrowed coordinator egress
(#0007), and allowed-root hardening (#0005) — are open.

M3's host-scoped network ceiling (#0001) has a precondition rather than a
parallel item. A ceiling scoped to a host can only be expressed as a permission
profile: the wire grant type carries a single bit, while a profile carries a
per-domain map, a per-socket map, and loopback binding as separately
controllable axes. #0021 moved the worker boundary onto profiles, gave the
operator a Codex home to define them in, and built a live harness that proves
all three axes in one run. What #0001 still decides is what the ceiling for a
real network-capable worker should be, how an operator declares it per project,
and whether the `network_proxy` feature is depended on outside a test home.

M3 is the current milestone. The two-tier constitution (#0003) is implemented
and covered offline: the overall document is a ceiling every project inherits,
every project must add one document that only narrows it, and a judge receives
its own project's document and no other. Its remaining evidence is the live
networked gate. It is the first configuration in which the
untrusted-evidence boundary is load-bearing for real ingested text rather than a
demo, and it is analyzed in
[the multi-capability injection assessment](multi-capability-injection-assessment.md).
M4 and M5 may proceed in parallel where independent.

M6 is deliberately deferred while the service remains a trusted, single-user,
loopback-only prototype. That deferral is a scope decision, not a claim of
safety. No milestone here grants permission to describe the service as safe for
remote, multi-user, or production authorization, and completing M6 is mandatory
before any such use.

Working assumptions: protect the governance boundary first; keep the natural
language constitution as guidance to a judge and never as a substitute for
code-enforced path, sandbox, permission, and session limits; keep operator
policy outside every coordinator- and worker-writable root. Update these
assumptions when the supported deployment model changes.
