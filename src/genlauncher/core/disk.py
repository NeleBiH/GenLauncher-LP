"""Disk usage of installed modifications."""

from __future__ import annotations

import os
from pathlib import Path

from .store import InstalledMod


def folder_size(folder: Path) -> int:
    """Total size in bytes of the files under ``folder``.

    Symlinks are not followed, so a mod linked into the game folder is never
    counted twice. Unreadable entries are skipped.
    """
    total = 0
    stack = [str(folder)]
    while stack:
        try:
            with os.scandir(stack.pop()) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                        elif entry.is_file(follow_symlinks=False):
                            total += entry.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue
        except OSError:
            continue
    return total


def installed_size(mod: InstalledMod, mods_root: Path) -> int:
    """Size of every installed version of ``mod``."""
    return sum(folder_size(mv.folder_name(mods_root)) for mv in mod.versions.values())


def format_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    raise AssertionError("unreachable")
