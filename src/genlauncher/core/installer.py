"""Archive extraction and installation into the mod store.

Everything is extracted into a TEMP_SUFFIX staging folder first and only moved into
place once it completes, so a crash mid-unpack leaves no half-installed version
(using the same suffix trick for safe replacement).

Member names coming out of an archive are never trusted: absolute paths, drive
letters, and `..` traversal all get rejected.
"""

from __future__ import annotations

import logging

import shutil
import subprocess
import tarfile
import zipfile
from pathlib import Path
from typing import Callable

from .config import CUSTOM_FILE_EXTENSIONS, TEMP_SUFFIX
from .models import ModificationVersion

log = logging.getLogger(__name__)

ARCHIVE_SUFFIXES = {".zip", ".7z", ".rar"}
BACKUP_SUFFIX = ".GLBAK"

ProgressCallback = Callable[[str], None]


class InstallError(RuntimeError):
    pass


class UnsafeArchiveEntry(InstallError):
    pass


# ---- path safety ---------------------------------------------------------


def safe_member_path(name: str) -> Path:
    """Validate an archive member name and return a safe relative path.

    Raises UnsafeArchiveEntry on anything that could escape the target directory.
    """
    if not name or name in (".", "./"):
        raise UnsafeArchiveEntry(f"empty member name: {name!r}")

    normalised = name.replace("\\", "/")

    if normalised.startswith("/"):
        raise UnsafeArchiveEntry(f"absolute path in archive: {name!r}")

    # Reject "C:..." style drive letters.
    if len(normalised) > 1 and normalised[1] == ":":
        raise UnsafeArchiveEntry(f"drive-letter path in archive: {name!r}")

    parts: list[str] = []
    for part in normalised.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            raise UnsafeArchiveEntry(f"path traversal in archive: {name!r}")
        parts.append(part)

    if not parts:
        raise UnsafeArchiveEntry(f"member resolves to nothing: {name!r}")

    return Path(*parts)


def _assert_inside(target: Path, member: str) -> Path:
    """Resolve a member under `target` and confirm it did not escape."""
    rel = safe_member_path(member)
    resolved = (target / rel).resolve()
    base = target.resolve()
    if resolved != base and base not in resolved.parents:
        raise UnsafeArchiveEntry(f"member escapes target directory: {member!r}")
    return resolved


# ---- extraction ----------------------------------------------------------


def _extract_zip(archive: Path, target: Path) -> None:
    with zipfile.ZipFile(archive) as zf:
        for info in zf.infolist():
            dest = _assert_inside(target, info.filename)
            if info.is_dir():
                dest.mkdir(parents=True, exist_ok=True)
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            # Reject symlinks stored inside the zip; we recreate links ourselves.
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise UnsafeArchiveEntry(f"symlink member not allowed: {info.filename!r}")
            with zf.open(info) as src, dest.open("wb") as out:
                shutil.copyfileobj(src, out, 1024 * 256)


def _extract_7z(archive: Path, target: Path) -> None:
    import py7zr

    with py7zr.SevenZipFile(archive, mode="r") as sz:
        for name in sz.getnames():
            _assert_inside(target, name)
        sz.extractall(path=target)


def _extract_rar(archive: Path, target: Path) -> None:
    """RAR needs an external tool; try rarfile, then 7z, then bsdtar."""
    try:
        import rarfile

        with rarfile.RarFile(archive) as rf:
            for name in rf.namelist():
                _assert_inside(target, name)
            rf.extractall(path=target)
        return
    except ImportError:
        pass
    except Exception as exc:  # rarfile raises its own errors
        log.debug("rarfile failed on %s: %s", archive, exc)

    for tool in ("7z", "7za", "7zz", "unar", "bsdtar"):
        if shutil.which(tool):
            if tool == "bsdtar":
                cmd = [tool, "-x", "-f", str(archive), "-C", str(target)]
            elif tool == "unar":
                cmd = [tool, "-quiet", "-force-overwrite", "-output-directory", str(target), str(archive)]
            else:
                cmd = [tool, "x", "-y", f"-o{target}", str(archive)]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
            if result.returncode == 0:
                return
            log.warning("%s failed on %s: %s", tool, archive, result.stderr[:400])

    raise InstallError(
        f"cannot extract {archive.name}: no RAR support. Install 'unrar', 'p7zip' or 'libarchive-tools'."
    )


def extract_archive(archive: Path, target: Path) -> None:
    """Extract zip/7z/rar into `target`."""
    archive = Path(archive)
    target = Path(target)
    target.mkdir(parents=True, exist_ok=True)

    suffix = archive.suffix.casefold()
    if suffix == ".zip":
        _extract_zip(archive, target)
    elif suffix == ".7z":
        _extract_7z(archive, target)
    elif suffix == ".rar":
        _extract_rar(archive, target)
    elif tarfile.is_tarfile(archive):
        with tarfile.open(archive) as tf:
            for member in tf.getmembers():
                _assert_inside(target, member.name)
            tf.extractall(path=target, filter="data")
    else:
        raise InstallError(f"unsupported archive format: {archive.name}")


def is_archive(path: Path) -> bool:
    return Path(path).suffix.casefold() in ARCHIVE_SUFFIXES


# ---- wrapping a single top-level folder ----------------------------------


def flatten_single_root(folder: Path) -> None:
    """If the archive produced exactly one directory and nothing else, descend.

    Mod archives are frequently wrapped in a single top folder; the game linker
    wants the *contents*, not the wrapper.
    """
    entries = list(folder.iterdir())
    if len(entries) != 1 or not entries[0].is_dir():
        return
    inner = entries[0]
    staging = folder.parent / f"{folder.name}{TEMP_SUFFIX}inner"
    staging.mkdir(parents=True, exist_ok=True)
    try:
        for child in inner.iterdir():
            shutil.move(str(child), str(staging / child.name))
        inner.rmdir()
        for child in staging.iterdir():
            shutil.move(str(child), str(folder / child.name))
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def detect_case_collisions(folder: Path) -> list[tuple[str, str]]:
    """Find relative paths in this tree that differ only by case.

    Linux keeps both, Windows/the game does not. A mod shipping Data/INI.big while
    the game has data/ini.big produces a duplicate that breaks loading, so the UI
    must warn about this.
    """
    collisions: list[tuple[str, str]] = []
    seen: dict[str, str] = {}
    for path in folder.rglob("*"):
        relative = str(path.relative_to(folder))
        key = relative.casefold()
        if key in seen and seen[key] != relative:
            collisions.append((seen[key], relative))
        else:
            seen.setdefault(key, relative)
    return collisions


# ---- atomic folder replacement (Rewritten PackageInstallFolderReplacer) ----


def reconcile_previous_replacement(final: Path, backup: Path) -> None:
    """Handle interrupted replacement from a previous run.

    If a backup exists but the installed folder does not, the previous
    replacement was interrupted between step 1 (move installed → backup)
    and step 2 (move staging → installed). Restore from backup.
    """
    if not backup.exists():
        return
    if not final.exists():
        log.warning("restoring interrupted replacement for %s from backup", final.name)
        backup.rename(final)
    else:
        log.info("removing stale backup for committed package %s", final.name)
        shutil.rmtree(backup, ignore_errors=True)


def atomic_replace_folder(staging: Path, final: Path) -> None:
    """Replace `final` with `staging` using a staged backup for rollback.

    Three phases (Rewritten PackageInstallFolderReplacer pattern):
      1. Move existing installed folder to backup
      2. Move staging folder into installed location
      3. On success: delete backup. On failure: rollback.

    If the process crashes between steps 1 and 2, the next call to
    reconcile_previous_replacement() will restore from backup.
    """
    backup = final.parent / f"{final.name}{BACKUP_SUFFIX}"

    # Recover from any previous interrupted replacement.
    reconcile_previous_replacement(final, backup)

    backup_created = False
    try:
        if final.exists():
            if backup.exists():
                shutil.rmtree(backup, ignore_errors=True)
            log.debug("moving existing %s to backup", final.name)
            final.rename(backup)
            backup_created = True

        log.debug("moving staging to %s", final.name)
        staging.rename(final)
    except Exception:
        # Rollback: restore from backup if we moved the original away.
        if backup_created and backup.exists() and not final.exists():
            log.warning("rolling back folder replacement for %s", final.name)
            try:
                backup.rename(final)
            except OSError as rb_exc:
                log.error("rollback failed for %s: %s", final.name, rb_exc)
        raise

    # Success: clean up the backup.
    if backup_created and backup.exists():
        try:
            shutil.rmtree(backup)
        except OSError as exc:
            log.warning(
                "replacement committed, but backup cleanup failed for %s: %s",
                final.name, exc,
            )


# ---- the install itself --------------------------------------------------


class Installer:
    """Installs an archive into the mod store, atomically."""

    def __init__(self, mods_root: Path) -> None:
        self._root = Path(mods_root)

    def staging_dir(self, mv: ModificationVersion) -> Path:
        """Sibling of the final folder, marked with TEMP_SUFFIX."""
        final = mv.folder_name(self._root)
        return final.parent / f"{final.name}{TEMP_SUFFIX}"

    def install(
        self,
        mv: ModificationVersion,
        archive: Path,
        progress: ProgressCallback | None = None,
    ) -> Path:
        """Unpack `archive` as `mv`. Returns the install folder.

        On any failure the staging folder is removed and nothing is touched.
        """
        archive = Path(archive)
        if not archive.is_file():
            raise InstallError(f"archive not found: {archive}")

        final = mv.folder_name(self._root)
        final.parent.mkdir(parents=True, exist_ok=True)

        staging = self.staging_dir(mv)
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True)

        note = progress or (lambda msg: None)
        try:
            if is_archive(archive) or tarfile.is_tarfile(archive):
                note(f"Unpacking {archive.name}...")
                extract_archive(archive, staging)
            else:
                # Some entries are a single file rather than an archive: the
                # ModdedExe entry is literally just modded.exe. Name it after
                # executable_file_name so the linker finds it.
                name = mv.executable_file_name or archive.name
                note(f"Storing {name}...")
                shutil.copy2(archive, staging / name)
            note("Preparing...")
            flatten_single_root(staging)

            # Safety: reject extracted trees with symlinks (Rewritten pattern).
            from .path_safety import UnsafePath, ensure_directory_tree_has_no_symlinks
            try:
                ensure_directory_tree_has_no_symlinks(staging, "Extracted archive paths")
            except UnsafePath as exc:
                raise InstallError(str(exc))

            # Store .big files as .gib so the game engine cannot accidentally
            # load them from the mod store (Rewritten BigFileVariantPath pattern).
            from .big import convert_big_to_gib
            renamed = convert_big_to_gib(staging)
            if renamed:
                log.debug("converted %d .big files to .gib in %s", renamed, staging)

            collisions = detect_case_collisions(staging)
            if collisions:
                for first, second in collisions[:5]:
                    log.warning("case collision: %s vs %s", first, second)

            # Atomic replacement with backup/rollback (Rewritten pattern).
            note("Installing...")
            atomic_replace_folder(staging, final)

            # Save checksums for integrity verification
            from .integrity import save_checksums

            try:
                note("Computing checksums...")
                save_checksums(final)
            except Exception:
                log.debug("checksum generation failed", exc_info=True)

            note(f"Installed to {final}")
            return final
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise

    def install_many(
        self,
        mv: ModificationVersion,
        sources: list[Path],
        progress: ProgressCallback | None = None,
    ) -> Path:
        """Install several archives/files/directories as one mod version.

        Everything is staged into a single folder and committed with one
        replace. Calling install() in a loop instead would wipe the staging
        area on every iteration, so only the last file would survive — which is
        exactly what "Add mod from files" used to do with a multi-file mod.
        """
        sources = [Path(s) for s in sources]
        if not sources:
            raise InstallError("no files selected")
        for src in sources:
            if not src.exists():
                raise InstallError(f"not found: {src}")

        final = mv.folder_name(self._root)
        final.parent.mkdir(parents=True, exist_ok=True)

        staging = self.staging_dir(mv)
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True)

        note = progress or (lambda msg: None)
        try:
            for src in sources:
                if src.is_dir():
                    note(f"Copying {src.name}/...")
                    shutil.copytree(src, staging, symlinks=True, dirs_exist_ok=True)
                elif is_archive(src) or tarfile.is_tarfile(src):
                    note(f"Unpacking {src.name}...")
                    extract_archive(src, staging)
                else:
                    # Single-file entries, e.g. ModdedExe shipping just
                    # modded.exe. Rename only when the entry is one file: with
                    # several sources the shared executable_file_name would make
                    # every file overwrite the same target and all but one would
                    # be lost.
                    name = (
                        mv.executable_file_name or src.name
                        if len(sources) == 1
                        else src.name
                    )
                    note(f"Storing {name}...")
                    shutil.copy2(src, staging / name)

            note("Preparing...")
            flatten_single_root(staging)

            from .path_safety import ensure_directory_tree_has_no_symlinks
            ensure_directory_tree_has_no_symlinks(staging, "Installed mod paths")

            # Store .big as .gib (Rewritten BigFileVariantPath pattern).
            from .big import convert_big_to_gib
            renamed = convert_big_to_gib(staging)
            if renamed:
                log.debug("converted %d .big files to .gib in %s", renamed, staging)

            collisions = detect_case_collisions(staging)
            for first, second in collisions[:5]:
                log.warning("case collision: %s vs %s", first, second)

            note("Installing...")
            atomic_replace_folder(staging, final)

            from .integrity import save_checksums

            try:
                note("Computing checksums...")
                save_checksums(final)
            except Exception:
                log.debug("checksum generation failed", exc_info=True)

            note(f"Installed to {final}")
            return final
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise

    def install_directory(self, mv: ModificationVersion, source: Path) -> Path:
        """Install from a folder the user selected (manual add)."""
        source = Path(source)
        if not source.is_dir():
            raise InstallError(f"not a directory: {source}")

        final = mv.folder_name(self._root)
        final.parent.mkdir(parents=True, exist_ok=True)
        staging = self.staging_dir(mv)
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)

        shutil.copytree(source, staging, symlinks=False)
        flatten_single_root(staging)

        from .path_safety import ensure_directory_tree_has_no_symlinks
        ensure_directory_tree_has_no_symlinks(staging, "Installed mod paths")

        atomic_replace_folder(staging, final)
        return final

    def remove(self, mv: ModificationVersion) -> bool:
        folder = mv.folder_name(self._root)
        staging = self.staging_dir(mv)
        backup = folder.parent / f"{folder.name}{BACKUP_SUFFIX}"
        removed = False
        for path in (staging, folder, backup):
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
                removed = True
        return removed