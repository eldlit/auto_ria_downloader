#!/usr/bin/env bash
set -euo pipefail

# Determine project root (directory containing this script).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [[ -z "${PYTHON_BIN:-}" ]]; then
    if [[ -x "$SCRIPT_DIR/.venv/bin/python" ]]; then
        PYTHON_BIN="$SCRIPT_DIR/.venv/bin/python"
    else
        PYTHON_BIN="python3"
    fi
fi

export PYTHONPATH="${PYTHONPATH:+$PYTHONPATH:}$SCRIPT_DIR/src"

DEFAULT_CONFIG="${CONFIG_PATH:-$SCRIPT_DIR/config.json}"
DEFAULT_INPUT="${INPUT_PATH:-$SCRIPT_DIR/input.txt}"

exec "$PYTHON_BIN" -m autoria_parser --clear-cache --config "$DEFAULT_CONFIG" --input "$DEFAULT_INPUT" "$@"
