# Supported security boundary

The supported deployment is one trusted operator and trusted local processes
on a single machine. The coordinator's judge and the operator-owned TOML file
are trusted; worker project files, prompts, approval requests, tool output,
and model verdicts are not. The deterministic approval policy validates
requests and prevents judge output from widening the original request or the
operator's permission ceiling.

The HTTP control plane is bound to `127.0.0.1` only, rejects browser `Origin`
headers and non-local `Host` headers, and has request limits. It has **no
authentication**. Any other local process running as the operator can call it;
malicious local processes are outside this threat model. Remote clients,
browser clients, and multiple users are unsupported. No flag enables remote
binding. Authentication, transport protection, and per-user authorization
would be required before supporting those modes.

Projects must be under startup-configured canonical allowed roots before
their `.codex/config.toml` is read. Existing symlinks that escape those roots
are rejected. A worker config symlink is accepted only when its resolved file
remains inside that worker project. The configured app-server endpoint must be a Unix socket owned
by the current user with no group/other permissions, inside an owner-controlled
directory. Workers receive explicit Codex sandbox policies with no network or
ambient temporary-directory write access. The coordinator process itself is
not network-sandboxed; its network privileges must be considered trusted.
Operator TOML must be an owner-controlled regular file outside every
worker-writable allowed root; symlinked or group/other-writable files fail
validation. Service-owned judging also requires a same-directory constitution
file and an explicit coordinator root. Both trusted policy files must be outside
the coordinator and worker roots. The constitution is snapshotted at startup;
HTTP verdict submissions are rejected in this mode. Explicit `external` mode
allows a trusted actor to submit verdicts but does not itself run a judge.

By default, stdout JSONL contains metadata only. `--verbose-events` opts into
payload output for the source-only live experiment; known secret-bearing field
names are redacted, but commands, prompts, diffs, and model messages can still
contain secrets. Full non-oversized managed events are retained only in memory,
under the limits in [the lifecycle contract](lifecycle.md). Run with `umask 077`
before redirecting stdout and remove logs on an operator-defined schedule.
The live experiment sets a private umask for its generated files. Its
coordinator JSONL log is created mode `0600` and refuses an existing file or
symlink rather than overwriting it.

The one-shot and service-owned `codex exec` judges request a deny-by-default filesystem
permission profile, grants reads only to Codex's minimal runtime paths, its
resolved installed runtime (and macOS system OpenSSL configuration if present),
and its empty temporary evidence directory. It disables auxiliary tools and
ignores user configuration and project rules. The exact profile has a denied-read regression
test. Before every judge invocation, the runner probes the same profile with
one allowed read, one unrelated-file read, and execution of the resolved Codex
binary; if the probe cannot run or the unrelated read succeeds, the judge fails
closed. The runtime denied-read regression test and a live `codex exec` judge
decision passed on an unrestricted operator host. The nested sandbox cannot
launch in the restricted development workspace. This check is specific to the
supported CLI and host; keep the fail-closed probe when changing either.
A caller-supplied judge in the Python API can operate on the frozen, minimal
`ApprovalCase` evidence, but the caller is responsible for isolating its own
implementation. This remains a requirement for caller-supplied judges.
