#!/usr/bin/env bash
# Pokreni GenLauncher GUI iz root foldera projekta.
#
#   ./run.sh          -> GUI
#   ./run.sh cli ...  -> CLI (npr. ./run.sh cli detect)
set -euo pipefail

cd "$(dirname "$0")"

VENV=".venv"

# ── Find Python >= 3.12 ─────────────────────────────────────
find_python() {
    for candidate in python3.14 python3.13 python3.12 python3; do
        if command -v "$candidate" &>/dev/null; then
            if "$candidate" -c "import sys; exit(0 if sys.version_info >= (3,12) else 1)" 2>/dev/null; then
                echo "$candidate"
                return
            fi
        fi
    done
}

# ── Auto-setup venv if missing ───────────────────────────────
if [ ! -d "$VENV" ]; then
    echo "Virtual environment not found. Setting up..."

    PY=$(find_python)
    if [ -z "$PY" ]; then
        echo "ERROR: Python >= 3.12 not found."
        echo "Install it:"
        echo "  Arch:   sudo pacman -S python"
        echo "  Debian: sudo apt install python3.12 python3.12-venv"
        echo "  Fedora: sudo dnf install python3.12"
        exit 1
    fi

    echo "Using $($PY --version)..."

    # Ensure venv module is available (Debian/Ubuntu)
    if ! "$PY" -m venv --help &>/dev/null 2>&1; then
        echo "Python venv module missing."
        pyver=$("$PY" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
        echo "Install it: sudo apt install python${pyver}-venv"
        exit 1
    fi

    "$PY" -m venv "$VENV"
    "$VENV/bin/pip" install --upgrade pip -q
    "$VENV/bin/pip" install -e ".[gui,rar]" -q
    echo "Setup complete."
    echo ""
fi

PY="$VENV/bin/python"

# ── Check PySide6 ────────────────────────────────────────────
if ! "$PY" -c "import PySide6" 2>/dev/null; then
    echo "PySide6 not installed. Installing dependencies..."
    "$VENV/bin/pip" install -e ".[gui,rar]" -q
fi

# ── Launch ───────────────────────────────────────────────────
if [ "$#" -eq 0 ]; then
    if [ -x "$VENV/bin/genlauncher-gui" ]; then
        exec "$VENV/bin/genlauncher-gui" "$@"
    fi
    exec "$PY" -m genlauncher.ui.app "$@"
fi

if [ "$1" = "cli" ]; then
    shift
    exec "$VENV/bin/genlauncher" "$@"
fi

exec "$PY" -m genlauncher.cli "$@"
