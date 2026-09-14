#!/bin/sh
set -eu

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
if [ "${1:-}" = "--installed-only" ]; then
    installed_only=1
elif [ "$#" -eq 0 ]; then
    installed_only=0
else
    echo "usage: release_gate.sh [--installed-only]" >&2
    exit 2
fi
gate_dir=$(mktemp -d)
trap 'rm -rf "$gate_dir"' EXIT HUP INT TERM

python3 -m build "$repo_root" --outdir "$gate_dir/dist"
python3 -m venv "$gate_dir/venv"
gate_python="$gate_dir/venv/bin/python"

set -- "$gate_dir"/dist/*.whl
if [ ! -f "$1" ]; then
    echo "release gate: wheel was not built" >&2
    exit 1
fi
"$gate_python" -m pip install "$1"

cd "$gate_dir"
"$gate_python" -c 'import codex_coordinator, codex_coordinator.api; path = codex_coordinator.__file__; print(path); raise SystemExit(0 if "/site-packages/" in path else "release gate: package import came from outside the installed wheel")'
"$gate_dir/venv/bin/codex-coordinator-service" --help >/dev/null
"$gate_dir/venv/bin/codex-coordinator-preflight" --help >/dev/null
if [ -n "${GENERIC_FIRST:-}" ] || [ -n "${GENERIC_SECOND:-}" ]; then
    : "${GENERIC_FIRST:?set GENERIC_FIRST when providing project paths}"
    : "${GENERIC_SECOND:?set GENERIC_SECOND when providing project paths}"
    "$gate_python" -m codex_coordinator.installed_smoke \
        --first "$GENERIC_FIRST" --second "$GENERIC_SECOND"
else
    "$gate_python" -m codex_coordinator.installed_smoke
fi
"$gate_python" -c 'from codex_coordinator.compatibility import check_codex_compatibility; print(check_codex_compatibility())'

if [ "$installed_only" -eq 1 ]; then
    echo "Installed-artifact gate passed; live generic gate was not run."
    exit 0
fi

: "${GENERIC_CONFIG:?set GENERIC_CONFIG to the absolute operator TOML path}"
: "${GENERIC_FIRST:?set GENERIC_FIRST to the first approved project path}"
: "${GENERIC_SECOND:?set GENERIC_SECOND to the second approved project path}"
: "${GENERIC_FIRST_GOAL:?set GENERIC_FIRST_GOAL to the first worker task}"
: "${GENERIC_SECOND_GOAL:?set GENERIC_SECOND_GOAL to the second worker task}"
: "${GENERIC_FOLLOW_UP:?set GENERIC_FOLLOW_UP to the first worker follow-up}"
: "${GENERIC_ALLOW_COMMAND:?set GENERIC_ALLOW_COMMAND to the exact approvable command}"
: "${GENERIC_DENY_COMMAND:?set GENERIC_DENY_COMMAND to the exact denied command}"

"$gate_python" "$repo_root/examples/generic_coordinator.py" \
    --config "$GENERIC_CONFIG" \
    --first "$GENERIC_FIRST" --second "$GENERIC_SECOND" \
    --first-goal "$GENERIC_FIRST_GOAL" \
    --second-goal "$GENERIC_SECOND_GOAL" \
    --follow-up "$GENERIC_FOLLOW_UP" \
    --allow-command "$GENERIC_ALLOW_COMMAND" \
    --deny-command "$GENERIC_DENY_COMMAND" \
    --require-outcomes
