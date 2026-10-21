#!/usr/bin/env python3
"""Pokreni GenLauncher GUI.

Primjeri:
    python src/run_genlauncher.py          # pokreni GUI
    python src/run_genlauncher.py --help   # prikaži pomoć (ako postoji CLI)
"""
import sys
from pathlib import Path

# Dodaj src/ na sys.path tako da se genlauncher paket pronađe
# bez potrebe za pip install -e
src_dir = Path(__file__).resolve().parent
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

from genlauncher.ui.app import run_gui

if __name__ == "__main__":
    raise SystemExit(run_gui())
