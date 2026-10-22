#!/usr/bin/env bash
# Offline setup + launcher for shellcraft (Linux).
# Needs Python 3.11+ installed first (see Install/README.md). No internet needed.
#
#   ./Install/setup.sh                 set up (first run) and start the interactive shell
#   ./Install/setup.sh --no-banner      any arguments are passed through to main.py
#   PYTHON=python3.12 ./Install/setup.sh   use a specific interpreter
set -euo pipefail

INSTALL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$INSTALL_DIR")"
WHEELS="$INSTALL_DIR/wheels"
VENV="$REPO_DIR/.venv"

# Pick an interpreter: $PYTHON, else the newest python3.x found, else python3.
find_python() {
    if [ -n "${PYTHON:-}" ]; then echo "$PYTHON"; return; fi
    for p in python3.14 python3.13 python3.12 python3.11 python3; do
        if command -v "$p" >/dev/null 2>&1; then echo "$p"; return; fi
    done
    return 1
}

PY="$(find_python)" || { echo "error: Python 3.11+ not found. Install it first (see Install/README.md)." >&2; exit 1; }
if ! "$PY" -c 'import sys; sys.exit(sys.version_info < (3, 11))'; then
    echo "error: $PY is $("$PY" -V 2>&1); shellcraft needs Python 3.11+." >&2
    exit 1
fi

if [ ! -x "$VENV/bin/python" ]; then
    echo ">> Creating virtual environment with $("$PY" -V 2>&1) in $VENV"
    if ! "$PY" -m venv "$VENV"; then
        echo "error: venv creation failed. On Debian/Ubuntu install the python3.x-venv package" >&2
        echo "       (included in Install/linux: sudo apt install ./Install/linux/*.deb)." >&2
        exit 1
    fi
fi

# Install only on the first run, or when requirements.txt changed (or a package went missing):
# pip takes a while even when everything is there, and this runs before every start.
STAMP="$VENV/.shellcraft-installed"
WANT="$(cat "$REPO_DIR/requirements.txt")"
if [ ! -f "$STAMP" ] || [ "$(cat "$STAMP")" != "$WANT" ] || \
   ! "$VENV/bin/python" -c 'import importlib.util as u, sys; sys.exit(not all(u.find_spec(m) for m in ("prompt_toolkit", "rich", "mcp", "requests", "pyte")))'; then
    echo ">> Installing dependencies from $WHEELS (offline)"
    "$VENV/bin/python" -m pip install -q --disable-pip-version-check --no-index --find-links "$WHEELS" \
        -r "$REPO_DIR/requirements.txt" "censys-platform>=0.16" "pytest>=8"
    printf '%s' "$WANT" > "$STAMP"
fi

echo ">> Starting shellcraft"
cd "$REPO_DIR"
exec "$VENV/bin/python" ./main.py "$@"
