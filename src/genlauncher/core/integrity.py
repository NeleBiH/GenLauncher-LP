"""File integrity checking via SHA-256 checksums.

Ported from Rewritten's integrity verification. Uses SHA-256 (not MD5) for
stronger guarantees. Computes and verifies hashes for installed mod files to
detect corruption or unauthorized modification.

Supports parallel hashing via concurrent.futures for large mod directories.
"""

from __future__ import annotations

import hashlib
import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

# Extensions that are checked for hash integrity
HASH_EXTENSIONS = frozenset(
    {".w3d", ".big", ".gib", ".bik", ".dds", ".tga", ".ini", ".scb", ".wnd", ".csf", ".str"}
)

# Extensions excluded from integrity checks
SKIP_EXTENSIONS = frozenset({".exe", ".dll"})

MANIFEST_NAME = ".genlauncher_checksums.json"
HASH_CHUNK = 1024 * 1024  # 1 MB (Rewritten DefaultBufferSize)
HASH_WORKERS = 4  # parallel hashing threads


@dataclass
class IntegrityResult:
    """Result of an integrity check."""

    ok: bool = True
    missing: list[str] = field(default_factory=list)
    corrupted: list[str] = field(default_factory=list)
    extra: list[str] = field(default_factory=list)
    # True when there was no stored manifest, so a fresh baseline was recorded
    # and nothing was actually verified. Reporting ok=True for this is
    # misleading: the first check after any manual install always "passes".
    no_baseline: bool = False

    @property
    def summary(self) -> str:
        parts = []
        if self.missing:
            parts.append(f"{len(self.missing)} missing")
        if self.corrupted:
            parts.append(f"{len(self.corrupted)} corrupted")
        if not parts:
            return "All files OK"
        return ", ".join(parts)


def _sha256(path: Path) -> str:
    """Compute SHA-256 hex digest of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(HASH_CHUNK)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest().upper()


def _should_check(path: Path) -> bool:
    """Whether this file should be included in integrity checks."""
    ext = path.suffix.lower()
    if ext in SKIP_EXTENSIONS:
        return False
    return ext in HASH_EXTENSIONS


def compute_checksums(mod_folder: Path) -> dict[str, str]:
    """Compute SHA-256 checksums for all eligible files in a mod folder.

    Uses parallel hashing when there are many files.
    Returns dict mapping relative path (forward slashes) to uppercase hex SHA-256.
    """
    checksums: dict[str, str] = {}
    if not mod_folder.is_dir():
        return checksums

    files = sorted(
        p for p in mod_folder.rglob("*")
        if p.is_file() and _should_check(p)
    )

    if len(files) <= 4:
        # Small number of files: hash sequentially (no thread overhead).
        for path in files:
            rel = path.relative_to(mod_folder).as_posix()
            try:
                checksums[rel] = _sha256(path)
            except OSError as exc:
                log.warning("cannot hash %s: %s", path, exc)
        return checksums

    # Parallel hashing for larger mod directories.
    def _hash_one(path: Path) -> tuple[str, str | None]:
        rel = path.relative_to(mod_folder).as_posix()
        try:
            return rel, _sha256(path)
        except OSError as exc:
            log.warning("cannot hash %s: %s", path, exc)
            return rel, None

    with ThreadPoolExecutor(max_workers=HASH_WORKERS) as pool:
        futures = {pool.submit(_hash_one, p): p for p in files}
        for future in as_completed(futures):
            rel, digest = future.result()
            if digest is not None:
                checksums[rel] = digest

    return checksums


def save_checksums(mod_folder: Path, checksums: dict[str, str] | None = None) -> Path:
    """Save checksums to a manifest file inside the mod folder.

    If checksums is None, computes them first. Returns the manifest path.
    """
    if checksums is None:
        checksums = compute_checksums(mod_folder)

    manifest = mod_folder / MANIFEST_NAME
    manifest.write_text(
        json.dumps(checksums, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return manifest


def verify_checksums(mod_folder: Path) -> IntegrityResult:
    """Verify installed mod files against saved checksums.

    Returns an IntegrityResult with details about any mismatches.
    """
    result = IntegrityResult()
    manifest = mod_folder / MANIFEST_NAME

    if not manifest.exists():
        log.info("no checksum manifest for %s, computing fresh", mod_folder.name)
        save_checksums(mod_folder)
        result.ok = True
        result.no_baseline = True
        return result

    try:
        expected = json.loads(manifest.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("cannot read checksum manifest: %s", exc)
        result.ok = False
        return result

    for rel_path, expected_hash in expected.items():
        full = mod_folder / rel_path
        if not full.exists():
            result.missing.append(rel_path)
            result.ok = False
            continue

        try:
            actual = _sha256(full)
        except OSError:
            result.missing.append(rel_path)
            result.ok = False
            continue

        if actual != expected_hash:
            result.corrupted.append(rel_path)
            result.ok = False

    return result
