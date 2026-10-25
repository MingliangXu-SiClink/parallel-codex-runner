#!/bin/sh

set -eu

usage() {
    printf '%s\n' \
        'Usage: install-pcrd.sh <server-url> [python-command]' \
        '' \
        'Set PCR_RELAY_MACHINE_ENROLLMENT_TOKEN before running, or enter it' \
        'when prompted. The token is never placed in shell history or argv.' \
        '' \
        'Installs the bundled PCR wheel in a dedicated virtual environment' \
        'and registers pcrd as a per-user service.' >&2
    exit 2
}

[ "$#" -ge 1 ] && [ "$#" -le 2 ] || usage

SERVER_URL=$1
PYTHON=${2:-python3}
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
WHEEL=$(find "$SCRIPT_DIR" -maxdepth 1 -type f \
    -name 'parallel_codex_runner-*.whl' -print | sort | tail -n 1)

ENROLLMENT_TOKEN=${PCR_RELAY_MACHINE_ENROLLMENT_TOKEN:-}
if [ -z "$ENROLLMENT_TOKEN" ]; then
    [ -t 0 ] || {
        printf '%s\n' \
            'PCR_RELAY_MACHINE_ENROLLMENT_TOKEN is required for non-interactive installation.' >&2
        exit 1
    }
    printf 'Server enrollment token: ' >&2
    trap 'stty echo 2>/dev/null || true; printf "\n" >&2' EXIT HUP INT TERM
    stty -echo
    IFS= read -r ENROLLMENT_TOKEN
    stty echo
    trap - EXIT HUP INT TERM
    printf '\n' >&2
fi

[ -n "$ENROLLMENT_TOKEN" ] || {
    printf '%s\n' 'Server enrollment token must not be empty.' >&2
    exit 1
}

[ -n "$WHEEL" ] || {
    printf 'PCR wheel is missing from %s\n' "$SCRIPT_DIR" >&2
    exit 1
}

VERSION=${WHEEL##*/parallel_codex_runner-}
VERSION=${VERSION%%-*}
case $(uname -s) in
    Darwin) DEFAULT_RUNTIME_ROOT="$HOME/Library/Application Support/parallel-codex-runner/runtime" ;;
    *) DEFAULT_RUNTIME_ROOT="${XDG_DATA_HOME:-$HOME/.local/share}/parallel-codex-runner/runtime" ;;
esac
RUNTIME_DIR="${PCR_RUNTIME_ROOT:-$DEFAULT_RUNTIME_ROOT}/$VERSION"
# Do not run unrelated editable-install hooks while bootstrapping the service.
"$PYTHON" -I -S -m venv "$RUNTIME_DIR"
RUNTIME_PYTHON="$RUNTIME_DIR/bin/python"
"$RUNTIME_PYTHON" -I -m pip install --upgrade "$WHEEL"
PCR_RELAY_MACHINE_ENROLLMENT_TOKEN=$ENROLLMENT_TOKEN \
    "$RUNTIME_PYTHON" -I -m parallel_codex_runner_core.remote.daemon install-service \
        --server-url "$SERVER_URL"
"$RUNTIME_PYTHON" -I -m parallel_codex_runner_core.remote.daemon service-status
