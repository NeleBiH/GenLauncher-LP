"""Discovering and choosing which executable the launcher starts.

Based on Rewritten's ``LauncherExecutableSelectionService`` which probes
every built-in name for the active game, appends user registrations, and
remembers the pick per game.

Nothing here downloads anything. ``modded.exe`` is a third-party binary
recognised when present but never fetched.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

# Executable kinds, matching Rewritten's BuiltInExecutableKind.
KIND_RETAIL = "retail"
KIND_COMMUNITY = "community"
KIND_GENERALS_ONLINE = "generals_online"
KIND_MODDED = "modded"
KIND_CUSTOM = "custom"

#: Names Rewritten probes for, in priority order.
#: ``modded.exe`` replaces ``generals.exe`` for Zero Hour only.
BUILT_IN_GAME_CLIENTS: dict[str, tuple[str, ...]] = {
    "zh": (
        "generalsonlinezh.exe",
        "generalszh.exe",
        "modded.exe",
        "generals.exe",
    ),
    "generals": ("generalsv.exe", "generals.exe"),
}

BUILT_IN_WORLD_BUILDERS: dict[str, tuple[str, ...]] = {
    # worldbuildernt27.exe is this port's own second candidate
    # (WORLD_BUILDER_EXE_CANDIDATES), kept alongside Rewritten's two.
    "zh": ("worldbuilder.exe", "worldbuildernt27.exe", "worldbuilderzh.exe"),
    "generals": ("worldbuilder.exe", "worldbuilderv.exe"),
}

RETAIL_NAMES = frozenset({"generals.exe", "worldbuilder.exe"})

#: UI labels for executable kinds.
KIND_LABELS = {
    KIND_RETAIL: "Retail",
    KIND_COMMUNITY: "TheSuperHackers",
    KIND_GENERALS_ONLINE: "GeneralsOnline",
    KIND_MODDED: "modded.exe",
    KIND_CUSTOM: "Custom",
}


class ExecutableError(ValueError):
    """A registration was rejected; the message is safe to show the user."""


@dataclass(frozen=True)
class ExecutableEntry:
    """One row of the management list.

    ``path`` is what gets stored and re-resolved: a bare file name when the
    executable sits in the game directory, an absolute path otherwise, exactly
    as Rewritten's ``NormalizeExecutablePath`` decides.
    """

    path: str
    display_name: str
    kind: str
    available: bool
    built_in: bool

    @property
    def can_remove(self) -> bool:
        return not self.built_in

    @property
    def can_edit(self) -> bool:
        return not self.built_in

    @property
    def label(self) -> str:
        """What the selection combo shows for this entry."""
        if self.kind == KIND_CUSTOM:
            return f"{self.display_name} — {self.path}"
        if self.display_name == self.path:
            return self.path
        return f"{self.display_name} ({self.path})"


def kind_of(file_name: str) -> str:
    """Classify a file name, mirroring ``BuiltInExecutable.ResolveKind``."""
    name = file_name.casefold()
    if name == "modded.exe":
        return KIND_MODDED
    if name in RETAIL_NAMES:
        return KIND_RETAIL
    if name == "generalsonlinezh.exe":
        return KIND_GENERALS_ONLINE
    return KIND_COMMUNITY


def normalize(game_dir: Path | str, path: str) -> str:
    """Store a path as a game-root file name when possible, else absolute.

    Rejects relative subpaths: an executable is either in the game directory
    or somewhere the user picked, never a half-resolved path that changes
    meaning with the working directory.
    """
    text = str(path).strip()
    if not text:
        raise ExecutableError("Enter a display name and select an executable.")

    candidate = Path(text)
    if not candidate.name.casefold().endswith(".exe"):
        raise ExecutableError("Executable file names must use the .exe extension.")

    if candidate.is_absolute():
        return str(candidate)

    if candidate.parent != Path("."):
        raise ExecutableError(
            "Use a file name in the game directory or a full path."
        )
    return candidate.name


def resolve(game_dir: Path | str, stored: str) -> Path | None:
    """Turn a stored selection into a real file, or None when it is missing.

    Matching ignores case because on Linux the filesystem is case-sensitive.
    """
    if not stored:
        return None
    game_dir = Path(game_dir)
    candidate = Path(stored)
    if candidate.is_absolute():
        return candidate if candidate.is_file() else None

    direct = game_dir / candidate
    if direct.is_file():
        return direct
    wanted = candidate.name.casefold()
    try:
        for entry in game_dir.iterdir():
            if entry.name.casefold() == wanted and entry.is_file():
                return entry
    except OSError:
        return None
    return None


def is_available(game_dir: Path | str, stored: str) -> bool:
    return resolve(game_dir, stored) is not None


def _custom_records(raw: Iterable[object]) -> list[tuple[str, str]]:
    """Pull ``(display_name, path)`` pairs out of the stored settings value.

    Settings JSON is user-editable, so anything malformed is dropped rather
    than raised: a broken entry must not stop the launcher from listing the
    executables that do work.
    """
    records: list[tuple[str, str]] = []
    if not isinstance(raw, (list, tuple)):
        return records
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = item.get("display_name")
        path = item.get("path")
        if isinstance(name, str) and isinstance(path, str) and name and path:
            records.append((name, path))
    return records


def discover(
    game_dir: Path | str,
    mode: str,
    target: str,
    custom: object = (),
) -> list[ExecutableEntry]:
    """List every executable of ``target`` ("game" or "world_builder").

    Built-in names are always listed, present or not: Rewritten shows a
    missing one with a cross instead of hiding it, so the user can see what
    the launcher would have used.
    """
    if target == "game":
        names: Sequence[str] = BUILT_IN_GAME_CLIENTS.get(
            mode, BUILT_IN_GAME_CLIENTS["zh"]
        )
    else:
        names = BUILT_IN_WORLD_BUILDERS.get(
            mode, BUILT_IN_WORLD_BUILDERS["zh"]
        )

    entries = [
        ExecutableEntry(
            path=name,
            display_name=KIND_LABELS[kind_of(name)],
            kind=kind_of(name),
            available=is_available(game_dir, name),
            built_in=True,
        )
        for name in names
    ]

    for display_name, path in _custom_records(custom):
        try:
            stored = normalize(game_dir, path)
        except ExecutableError:
            continue
        entries.append(
            ExecutableEntry(
                path=stored,
                display_name=display_name,
                kind=KIND_CUSTOM,
                available=is_available(game_dir, stored),
                built_in=False,
            )
        )
    return entries


def select_entry(
    entries: Sequence[ExecutableEntry], selected: str
) -> ExecutableEntry | None:
    """Find the saved pick, else the first usable entry.

    Mirrors ``LauncherExecutableSelectionService.SelectPreferredOption``: a
    stale selection must fall back rather than leave the launcher pointing at
    nothing.
    """
    if selected:
        for entry in entries:
            if entry.path.casefold() == selected.casefold():
                return entry
    for entry in entries:
        if entry.available:
            return entry
    return entries[0] if entries else None


def validate_registration(
    game_dir: Path | str,
    display_name: str,
    path: str,
    existing: Sequence[ExecutableEntry],
    editing: str = "",
) -> tuple[str, str]:
    """Check an add/edit draft.

    Returns ``(display_name, stored_path)`` or raises :class:`ExecutableError`
    with one of the reference's validation messages.
    """
    name = display_name.strip()
    if not name:
        raise ExecutableError("Enter a display name and select an executable.")

    stored = normalize(game_dir, path)

    others = [e for e in existing if e.path.casefold() != editing.casefold()]
    if any(e.display_name.casefold() == name.casefold() for e in others):
        raise ExecutableError("An entry with this display name already exists.")
    if any(e.path.casefold() == stored.casefold() for e in others):
        raise ExecutableError("This executable is already registered.")
    if not is_available(game_dir, stored):
        raise ExecutableError("The selected executable is missing or cannot be used.")
    return name, stored
