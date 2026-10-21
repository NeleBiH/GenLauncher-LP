"""Filesystem path safety checks.

Based on Rewritten's filesystem path safety checks.

Prevents symlink-based attacks and path traversal by rejecting paths
that contain symbolic links anywhere in their chain. This matters for
mod deployment: a malicious archive could place a symlink pointing
outside the game directory, causing the linker to overwrite system files.
"""

from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)


class UnsafePath(Exception):
    """A path failed a safety check (contains symlinks or escapes root)."""


def ensure_path_chain_has_no_symlinks(path: Path, subject: str = "Paths") -> None:
    """Reject a path if any existing ancestor is a symlink.

    Walks from the filesystem root down to `path`, checking each segment.
    Stops at the first non-existent segment (nothing to check beyond it).

    Raises UnsafePath if any segment is a symlink.
    """
    path = Path(path).absolute()
    parts = path.parts
    current = Path(parts[0])  # root (/ on Linux)
    for part in parts[1:]:
        current = current / part
        if not current.exists() and not current.is_symlink():
            break
        if current.is_symlink():
            raise UnsafePath(
                f"{subject} must not contain symbolic links: {current}"
            )


def ensure_directory_tree_has_no_symlinks(
    directory: Path, subject: str = "Paths",
) -> None:
    """Reject a directory tree if any entry is a symlink.

    Recursively walks the tree without following symlinks.
    """
    directory = Path(directory)
    if not directory.is_dir():
        return
    if directory.is_symlink():
        raise UnsafePath(f"{subject} root is a symbolic link: {directory}")

    for entry in directory.rglob("*"):
        if entry.is_symlink():
            raise UnsafePath(
                f"{subject} must not contain symbolic links: {entry}"
            )


def ensure_path_inside_root(
    path: Path, root: Path, subject: str = "Paths",
) -> Path:
    """Resolve a path and verify it stays inside root.

    Returns the resolved path.
    Raises UnsafePath if it escapes.
    """
    resolved = Path(path).resolve()
    root_resolved = Path(root).resolve()
    try:
        resolved.relative_to(root_resolved)
    except ValueError:
        raise UnsafePath(
            f"{subject} must stay inside {root_resolved}: {resolved}"
        )
    return resolved


def safe_resolve(
    root: Path, candidate: Path, subject: str = "Paths",
) -> Path:
    """Resolve a candidate path inside root with full safety checks.

    1. Verifies the path stays inside root
    2. Checks no ancestor is a symlink
    3. Returns the resolved path
    """
    resolved = ensure_path_inside_root(candidate, root, subject)
    ensure_path_chain_has_no_symlinks(root, subject)
    ensure_path_chain_has_no_symlinks(candidate, subject)
    return resolved


def list_files_no_symlinks(directory: Path, subject: str = "Paths") -> list[Path]:
    """List all files in a directory tree, rejecting symlinks.

    Returns list of real file paths. Raises UnsafePath if any symlink found.
    """
    ensure_directory_tree_has_no_symlinks(directory, subject)
    return [p for p in directory.rglob("*") if p.is_file()]
