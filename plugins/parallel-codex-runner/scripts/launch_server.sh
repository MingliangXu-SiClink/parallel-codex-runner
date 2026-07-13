#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PLUGIN_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
REPO_ROOT=$(CDPATH= cd -- "$PLUGIN_DIR/../.." && pwd)

try_python() {
    candidate=$1
    if [ -z "$candidate" ] || [ ! -x "$candidate" ]; then
        return 1
    fi
    if PYTHONPATH="$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}" "$candidate" -c \
        'import mcp.server.fastmcp; import parallel_codex_runner_core.plugin_mcp' \
        >/dev/null 2>&1; then
        exec env PYTHONPATH="$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}" \
            "$candidate" -m parallel_codex_runner_core.plugin_mcp
    fi
    return 1
}

if [ -n "${PCR_PYTHON:-}" ]; then
    try_python "$PCR_PYTHON" || {
        printf '%s\n' "PCR_PYTHON cannot import PCR and FastMCP: $PCR_PYTHON" >&2
        exit 2
    }
fi

for name in python3 python; do
    candidate=$(command -v "$name" 2>/dev/null || true)
    try_python "$candidate" || true
done

for candidate in /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3; do
    try_python "$candidate" || true
done

printf '%s\n' \
    "No Python interpreter can import parallel-codex-runner and FastMCP." \
    "Install PCR, or set PCR_PYTHON to the absolute interpreter path." >&2
exit 2
