# Goal: installable coordinator for generic local workflows

## Status

Complete. All six children are complete for the supported single-user local
workflow. The preflight command, compatibility gate, installed-wheel fixture,
and strict generic live gate have implementation and tests. On the operator
host, the installed-artifact and generic live gates passed, including accepted
and declined exact-command approvals, both terminal turns, and a completed
follow-up. The separate live one-shot `codex exec` judge gate returned
`{"liveJudgeGate":"passed","profile":"coordinator_judge","verdict":"deny"}`;
it would reject a runner error or an invalid response. The judge's
pre-invocation probe also checks an allowed read, an unrelated denied read,
and execution of the resolved Codex binary under the same profile. The hosted
macOS [Verify run](https://github.com/joshpearce/codex_coordinator/actions/runs/34751671527)
passed 177 tests and the installed-wheel gate. A real app-server initialization
handshake passes with disposable local state.
The owner intentionally chose to keep the package unlicensed, so license
metadata is omitted. This is the parent implementation goal for turning the
existing Python package and local service into a reusable coordinator. It
does not claim that the service is safe for remote, multi-user, or production
deployment.

## Outcome

A user can install the built package into a clean Python environment, configure
trusted policy for arbitrary worker projects, and run a documented multi-worker
Codex workflow without copying or importing the inventory demonstration. The
coordinator exposes stable session, event, approval, follow-up, and shutdown
operations while preserving the existing deterministic approval boundary.

## Child issues

1. [Expose a reusable coordination API](generic-coordination-api.md)
2. [Externalize trusted runtime configuration](trusted-runtime-configuration.md)
3. [Make the CLI and example portable](portable-cli-and-example.md)
4. [Define session, approval, and event lifecycles](session-lifecycle-and-events.md)
5. [Enforce the supported security boundary](generic-coordination-security.md)
6. [Verify the installed artifact and Codex compatibility](installed-artifact-and-compatibility.md)

The child issues may be implemented in stages, but this goal is complete only
when all six are complete and their integration criteria pass. Existing security
issues linked from the security child issue remain authoritative for their
individual fixes; closing this goal must not silently mark them resolved.

## End-to-end acceptance criteria

- Build and install a wheel outside this checkout in a fresh supported Python
  environment. Both console entry points and the documented Python API work
  without repository-relative files or `uv` at runtime.
- Configure two unrelated, operator-approved worker projects, a judge, and
  permission ceilings using documented inputs. Worker-controlled files or
  prompts cannot enlarge those ceilings.
- Start concurrent workers, observe correlated events, resolve an allowed
  approval and a denial, send a follow-up, and reach terminal states through
  the reusable interface. The example uses no inventory-specific goal or code.
- Demonstrate timeout/cancellation and connection-loss behavior without leaving
  an approval indefinitely pending or silently granting authority.
- Run unit, installed-wheel, protocol-compatibility, and generic end-to-end
  checks in CI or a documented release gate.
- Document the supported single-user local threat model and explicitly state
  which controls are required before remote or multi-user use.
