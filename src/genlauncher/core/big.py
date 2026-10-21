"""SAGE BIG archive detection, naming rules, and camera height patching.

The BIG container layout is read from the documented format: a four-character
code `BIGF` (uncompressed) or `BIG4` (compressed), a big-endian entry count,
then per entry a big-endian offset and length followed by a null-terminated
filename.

Camera height patching rewrites `MaxCameraHeight` inside the `GameData.ini`
stored in a .big archive. This module handles parsing and bounds-checked
rewriting of the value.

The `.big` → `.gib` naming comes from the GPL-3.0 Rewritten reference
(BigFileVariantPath); see convert_big_to_gib.
"""

from __future__ import annotations

import logging
import shutil
import struct
from pathlib import Path

from .config import ORIGINAL_SUFFIX, REPLACE_SUFFIX

log = logging.getLogger(__name__)

# A .big of exactly this size is header-only (empty archive) and is skipped
# during linking unless the caller explicitly asks for empty bigs.
EMPTY_BIG_SIZE = 24

BIG_SIGNATURES = (b"BIGF", b"BIG4")
PLACEHOLDER_PREFIX = b"??"


def is_big_archive(path: Path) -> bool:
    """True if `path` is a SAGE BIG archive (or a `??` placeholder).

    Never raises: an unreadable file is simply not a BIG.
    """
    try:
        with open(path, "rb") as fh:
            head = fh.read(4)
            if len(head) < 4:
                # Original reads two bytes and compares against '??'.
                return head[:2] == PLACEHOLDER_PREFIX
            return head in BIG_SIGNATURES
    except OSError:
        return False


def is_empty_big(path: Path) -> bool:
    """True if this BIG is a header-only placeholder."""
    try:
        return path.stat().st_size == EMPTY_BIG_SIZE
    except OSError:
        return False


def big_target_name(name: str) -> str:
    """Force the `.big` extension, mirroring Path.ChangeExtension(target, "big").

    A mod shipping `INI.BIG` must still link over the game's `INI.big`. On Linux
    that difference is real, so we normalise explicitly.
    Also handles `.gib` → `.big` deployment conversion.
    """
    path = Path(name)
    return str(path.with_suffix(".big"))


def is_gib_file(path: Path) -> bool:
    """True if the path has a `.gib` extension (case-insensitive)."""
    return path.suffix.casefold() == ".gib"


def convert_big_to_gib(directory: Path) -> int:
    """Rename all `.big` files in a directory tree to `.gib`.

    Ported from Rewritten BigFileVariantPath: at installation time `.big` files
    are stored as `.gib` so the game engine cannot accidentally load them from
    the mod store. The linker reverses this at deployment time.

    Returns the number of files renamed.
    """
    count = 0
    for big_file in list(directory.rglob("*")):
        if big_file.suffix.casefold() != ".big":
            continue
        if big_file.is_symlink():
            continue
        gib_path = big_file.with_suffix(".gib")
        if gib_path.exists():
            gib_path.unlink()
        big_file.rename(gib_path)
        count += 1
    return count


def is_parked(name: str) -> bool:
    return name.endswith(REPLACE_SUFFIX) or name.endswith(ORIGINAL_SUFFIX)


def strip_parked_suffix(name: str) -> str:
    """Remove a trailing `.GLR` / `.GOF` from a name.

    A patch may ship `INI.GLR` to mean "link this over INI.big".
    """
    for suffix in (REPLACE_SUFFIX, ORIGINAL_SUFFIX):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


# ---------------------------------------------------------------------------
# Camera height .big patching
# ---------------------------------------------------------------------------

# Inside .big archives, paths use Windows backslashes. But some mods might use
# forward slashes, so we normalise both separators when comparing.
GAMEDATA_INI_VARIANTS = {"data\\ini\\gamedata.ini"}


def _read_big_entries(fh) -> list[tuple[str, int, int]]:
    """Parse .big archive header, return list of (name, offset, length)."""
    magic = fh.read(4)
    if magic not in BIG_SIGNATURES:
        return []

    fh.read(4)  # skip (LE uint32)
    entry_count = struct.unpack(">I", fh.read(4))[0]
    fh.read(4)  # skip (BE uint32)

    entries = []
    for _ in range(entry_count):
        offset = struct.unpack(">I", fh.read(4))[0]
        length = struct.unpack(">I", fh.read(4))[0]
        # Read null-terminated filename
        chars = []
        while True:
            b = fh.read(1)
            if not b or b == b"\x00":
                break
            chars.append(b)
        name = b"".join(chars).decode("ascii", errors="replace")
        entries.append((name, offset, length))
    return entries


def contains_gamedata_ini(path: Path) -> bool:
    """Check if a .big archive contains Data\\INI\\GameData.ini."""
    if not is_big_archive(path):
        return False
    try:
        with open(path, "rb") as fh:
            for name, _, _ in _read_big_entries(fh):
                if name.lower().replace("/", "\\") in GAMEDATA_INI_VARIANTS:
                    return True
    except OSError:
        pass
    return False


def find_big_with_gamedata(mod_folders: list[Path]) -> Path | None:
    """Find the first .big file containing GameData.ini across mod folders."""
    for folder in mod_folders:
        if not folder.is_dir():
            continue
        for big_file in sorted(folder.rglob("*.big")):
            if contains_gamedata_ini(big_file):
                return big_file
    return None


def _locate_camera_height(content: str) -> tuple[int, int, int, int] | None:
    """Locate the MaxCameraHeight value inside GameData.ini text.

    Returns (span_start, span_end, decimals, lead) where span_start/span_end
    delimit the bytes after '=' up to (but excluding) the line terminator,
    decimals is the digit count the file uses after the decimal point, and lead
    is the length of the whitespace padding in front of the value. Returns None
    when the line cannot be parsed.
    """
    idx = content.find("MaxCameraHeight")
    if idx < 0:
        return None
    eq = content.find("=", idx + len("MaxCameraHeight"))
    if eq < 0:
        return None

    # Everything between '=' and the line terminator is ours to overwrite, but
    # never the terminator itself: clobbering it welds this line to the next.
    tail_start = eq + 1
    line_end = len(content)
    for i in range(tail_start, len(content)):
        if content[i] in "\r\n":
            line_end = i
            break

    raw = content[tail_start:line_end]
    lead = len(raw) - len(raw.lstrip(" \t"))
    stripped = raw.strip()
    if not stripped or any(c not in "0123456789." for c in stripped):
        return None
    if "." not in stripped:
        decimals = 0
    else:
        decimals = len(stripped.split(".", 1)[1])
    return tail_start, line_end, decimals, lead


def _format_camera_value(height: int, decimals: int) -> str:
    """Render the replacement using the file's own decimal precision."""
    return f"{height}.{'0' * decimals}" if decimals else str(height)


def set_camera_height(big_path: Path, height: int) -> bool:
    """Patch MaxCameraHeight inside GameData.ini in a .big archive.

    Creates a .GOF backup before modifying. Returns True on success.

    Parses the existing value, keeps the file's own decimal precision, and
    refuses to write when the result would not fit inside the line.
    """
    if height <= 0:
        return False

    try:
        with open(big_path, "rb") as fh:
            entries = _read_big_entries(fh)
            target = None
            for name, offset, length in entries:
                if name.lower().replace("/", "\\") not in GAMEDATA_INI_VARIANTS:
                    continue
                fh.seek(offset)
                content = fh.read(length).decode("ascii", errors="replace")
                found = _locate_camera_height(content)
                if found is None:
                    log.warning("MaxCameraHeight not parseable in %s", big_path)
                    return False
                span_start, span_end, decimals, lead = found
                target = (offset + span_start, offset + span_end, decimals, lead)
                break

        if target is None:
            log.warning("GameData.ini not found in %s", big_path)
            return False

        write_pos, span_end, decimals, lead = target
        available = span_end - write_pos
        value_str = _format_camera_value(height, decimals)
        if lead + len(value_str) > available:
            log.error(
                "Camera height %d needs %d bytes but only %d are free in %s; "
                "refusing to write",
                height, lead + len(value_str), available, big_path.name,
            )
            return False

        # Keep the file's own leading padding and pad out to the original width
        # so the line terminator stays exactly where it was.
        payload = (" " * lead + value_str).ljust(available).encode("ascii")

        backup = big_path.with_name(big_path.name + ORIGINAL_SUFFIX)
        if not backup.exists():
            shutil.copy2(big_path, backup)

        with open(big_path, "r+b") as wfh:
            wfh.seek(write_pos)
            wfh.write(payload)

        log.info(
            "Set camera height to %d in %s (offset %d, %d bytes)",
            height, big_path.name, write_pos, len(payload),
        )
        return True
    except OSError as exc:
        log.error("Failed to patch camera height in %s: %s", big_path, exc)
        return False


def restore_camera_height(big_path: Path) -> bool:
    """Restore the original .big file from the .GOF backup."""
    backup = big_path.with_name(big_path.name + ORIGINAL_SUFFIX)
    if not backup.exists():
        return False
    try:
        shutil.copy2(backup, big_path)
        backup.unlink()
        log.info("Restored original %s from backup", big_path.name)
        return True
    except OSError as exc:
        log.error("Failed to restore %s: %s", big_path, exc)
        return False