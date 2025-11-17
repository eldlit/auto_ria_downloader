#!/usr/bin/env bash
set -euo pipefail

# Determine project root (directory containing this script).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

PYTHON_BIN="${PYTHON_BIN:-python3}"

exec "$PYTHON_BIN" -m autoria_parser --clear-cache "$@"
