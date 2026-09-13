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
validation.

By default, stdout JSONL contains metadata only. `--verbose-events` opts into
payload output for the source-only live experiment; known secret-bearing field
names are redacted, but commands, prompts, diffs, and model messages can still
contain secrets. Full non-oversized managed events are retained only in memory,
under the limits in [the lifecycle contract](lifecycle.md). Run with `umask 077`
before redirecting stdout and remove logs on an operator-defined schedule.
The live experiment sets a private umask for its generated files. Its
coordinator JSONL log is created mode `0600` and refuses an existing file or
symlink rather than overwriting it.

The one-shot `codex exec` judge now requests a deny-by-default filesystem
permission profile, grants reads only to Codex's minimal runtime paths and its
empty temporary evidence directory, disables auxiliary tools, and ignores user
configuration and project rules. The exact profile has a denied-read regression
test. Before every judge invocation, the runner probes the same profile with
one allowed read and one unrelated-file read; if the probe cannot run or the
unrelated read succeeds, the judge fails closed. The runtime denied-read
regression test passed on an unrestricted operator host, but it cannot launch
a nested sandbox in the current development workspace. A live `codex exec`
judge turn using the same profile still needs verification before claiming
that judge-turn read isolation is established. Do not use this judge when
unrelated files or secrets must be hidden from it until that check passes.
A caller-supplied judge in the Python API can operate on the frozen, minimal
`ApprovalCase` evidence, but the caller is responsible for isolating its own
implementation. This is a remaining requirement before the generic security
issue can close.
