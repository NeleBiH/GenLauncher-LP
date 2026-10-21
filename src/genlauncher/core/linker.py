"""Linking a selected mod set into the game folder, and unlinking afterwards.

This is the part that can damage a game install, so it is deliberately paranoid:

* Every link we create is written to a journal (`links.json`) before it is made, so
  a crash of either the launcher *or the game* can be cleaned up on next start.
* We never overwrite a real game file. A file we cannot move aside is reported,
  not clobbered.
* Cleanup runs at startup whenever a journal is found.
* Recursive walks never follow directory symlinks, because we *create* directory
  symlinks that point back into the mod store - following them would loop forever.

Ordering: mods, then patches, then addons, then executables — later entries win.
"""

from __future__ import annotations

import errno
import json
import logging
import os
import shutil
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Iterator

from .big import big_target_name, is_big_archive, is_empty_big, is_gib_file, strip_parked_suffix
from .config import (
    CUSTOM_FILE_EXTENSIONS,
    ORIGINAL_SUFFIX,
    REPLACE_SUFFIX,
    STEAM_COMMON_SUBDIR,
)
from .models import ModificationVersion, ModType

log = logging.getLogger(__name__)

# A mod's own .exe/.dll are never linked into the game folder. DLL injection is
# how a broken mod bricks a Windows/Proton game, and executables are handled
# by their own dedicated code path.
MOD_EXCLUDED_EXTENSIONS = frozenset({".exe", ".dll"})

# Restored whenever no mod is selected.
BASE_SCRIPT_FILES = (
    "Data/Scripts/MultiplayerScripts.scb",
    "Data/Scripts/Scripts.ini",
    "Data/Scripts/SkirmishScripts.scb",
)


class LinkStrategy(str, Enum):
    SYMLINK = "symlink"
    HARDLINK = "hardlink"
    COPY = "copy"

    @classmethod
    def parse(cls, value: "str | LinkStrategy | None") -> "LinkStrategy":
        if isinstance(value, cls):
            return value
        if not value:
            return cls.SYMLINK
        try:
            return cls(str(value).strip().casefold())
        except ValueError:
            log.warning("unknown link strategy %r, using symlink", value)
            return cls.SYMLINK


@dataclass
class LinkRecord:
    """One journal entry: something we created and must undo."""

    kind: str  # "file" | "dir"
    target: str  # path inside the game folder
    source: str = ""  # what it points at (or what it replaced)
    strategy: str = "symlink"
    replaced: str = ""  # parked original file, if any
    created_at: float = field(default_factory=time.time)


@dataclass
class LinkReport:
    linked: list[LinkRecord] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    replaced_originals: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


class Journal:
    """Crash-recovery log of everything we linked."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)

    @property
    def path(self) -> Path:
        return self._path

    def exists(self) -> bool:
        return self._path.is_file()

    def load(self) -> list[LinkRecord]:
        if not self._path.is_file():
            return []
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            log.warning("unreadable journal at %s, treating as empty", self._path)
            return []
        entries = raw.get("links", []) if isinstance(raw, dict) else []
        out: list[LinkRecord] = []
        for entry in entries:
            if isinstance(entry, dict) and entry.get("target"):
                out.append(
                    LinkRecord(
                        kind=str(entry.get("kind", "file")),
                        target=str(entry["target"]),
                        source=str(entry.get("source", "")),
                        strategy=str(entry.get("strategy", "symlink")),
                        replaced=str(entry.get("replaced", "")),
                        created_at=float(entry.get("created_at", 0.0)),
                    )
                )
        return out

    def save(self, records: list[LinkRecord]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "links": [
                {
                    "kind": r.kind,
                    "target": r.target,
                    "source": r.source,
                    "strategy": r.strategy,
                    "replaced": r.replaced,
                    "created_at": r.created_at,
                }
                for r in records
            ],
        }
        tmp = self._path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(self._path)

    def clear(self) -> None:
        self._path.unlink(missing_ok=True)


def walk_nofollow(root: Path) -> Iterator[tuple[Path, list[Path], list[Path]]]:
    """os.walk that never descends into directory symlinks.

    Mandatory here: linking creates `game/Data -> store/Mod/Data`, so a naive
    rglob('*') would recurse back into the mod store through our own link.
    """
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        here = Path(dirpath)
        dirnames.sort()
        yield here, [here / d for d in dirnames], [here / f for f in sorted(filenames)]


class Linker:
    def __init__(
        self,
        game_dir: Path,
        journal_path: Path,
        mods_root: Path,
        strategy: "str | LinkStrategy" = LinkStrategy.SYMLINK,
        game_files: set[str] | None = None,
    ) -> None:
        self._game_dir = Path(game_dir).resolve()
        self._mods_root = Path(mods_root)
        self.strategy = LinkStrategy.parse(strategy)
        self._journal = Journal(journal_path)
        self._game_files = {g.casefold() for g in (game_files or set())}

    @property
    def journal(self) -> Journal:
        return self._journal

    @property
    def game_dir(self) -> Path:
        return self._game_dir

    @property
    def has_pending_links(self) -> bool:
        return self._journal.exists()

    # ---- startup safety -------------------------------------------------

    def cleanup(self) -> LinkReport:
        """Undo whatever a previous run left behind.

        Called before every link session. Also sweeps parked suffixes, which is
        what recovers a game folder after a hard kill that lost the journal.
        """
        report = LinkReport()
        records = self._journal.load()
        for record in records:
            self._undo(record, report)
        if records:
            self._journal.clear()
        self._sweep_parked(report)
        return report

    def _sweep_parked(self, report: LinkReport) -> None:
        """Restore everything parked with REPLACE_SUFFIX."""
        for suffix in (REPLACE_SUFFIX, ORIGINAL_SUFFIX):
            for parked in sorted(self._game_dir.rglob(f"*{suffix}")):
                if not (parked.is_file() or parked.is_symlink()):
                    continue
                original = Path(str(parked)[: -len(suffix)])
                if original.is_symlink() or original.exists():
                    # Already restored; the parked copy is redundant.
                    _safe_unlink(parked)
                    continue
                try:
                    parked.rename(original)
                    report.replaced_originals.append(str(original))
                except OSError as exc:
                    report.errors.append(f"could not restore {original.name}: {exc}")

    # ---- link / unlink --------------------------------------------------

    def link(
        self,
        versions: list[ModificationVersion],
        link_empty_bigs: bool = False,
        set_camera_height: bool = False,
    ) -> LinkReport:
        """Link the given set into the game folder.

        `link_empty_bigs` controls whether header-only .big files are linked;
        only applies to the top level of the main mod.
        """
        self.cleanup()
        report = LinkReport()
        records: list[LinkRecord] = []

        # Safety: reject game dir or mod sources that contain symlinks
        # in their path chain (Rewritten FileSystemPathSafety pattern).
        from .path_safety import UnsafePath, ensure_path_chain_has_no_symlinks
        try:
            ensure_path_chain_has_no_symlinks(self._game_dir, "Game directory paths")
        except UnsafePath as exc:
            report.errors.append(str(exc))
            return report

        self._park_originals(report)

        ordered = self._ordered(versions)
        for mv in ordered:
            source = mv.folder_name(self._mods_root)
            if not source.is_dir():
                report.errors.append(f"not installed: {mv.name} {mv.version}")
                continue
            try:
                ensure_path_chain_has_no_symlinks(source, "Mod source paths")
            except UnsafePath as exc:
                report.errors.append(str(exc))
                continue
            self._link_folder(
                source, mv, report, records, link_empty_bigs=link_empty_bigs
            )

        if not ordered:
            self._restore_base_scripts(report)

        self._journal.save(records)
        report.linked = records
        return report

    @staticmethod
    def _ordered(versions: list[ModificationVersion]) -> list[ModificationVersion]:
        """Sort so later links win: mod, patches, addons, then executables."""
        rank = {
            ModType.MOD: 0,
            ModType.PATCH: 1,
            ModType.ADDON: 2,
            ModType.EXECUTABLE: 3,
            ModType.ADVERTISING: 4,
        }
        return sorted(
            versions, key=lambda v: (rank.get(v.mod_type, 9), v.name.casefold(), v.version)
        )

    def unlink(self) -> LinkReport:
        """Remove every link we made and restore parked originals."""
        report = LinkReport()
        for record in self._journal.load():
            self._undo(record, report)
        self._journal.clear()
        self._sweep_parked(report)
        return report

    def _undo(self, record: LinkRecord, report: LinkReport) -> None:
        target = Path(record.target)
        try:
            if target.is_symlink():
                target.unlink()
            elif record.kind == "dir" and target.is_dir():
                # Only ever a directory we created for the copy/hardlink strategy.
                shutil.rmtree(target, ignore_errors=True)
            elif record.strategy == LinkStrategy.COPY.value and target.is_file():
                if not _looks_like_our_copy(record, target):
                    # Someone replaced our copy with their own file. Leave it alone.
                    report.errors.append(
                        f"{target.name} no longer matches the installed mod; not removed"
                    )
                    return
                target.unlink()
            elif target.exists():
                target.unlink()
        except OSError as exc:
            report.errors.append(f"could not remove {target}: {exc}")

        if record.replaced:
            self._restore_parked(Path(record.replaced), report)

    def _restore_parked(self, parked: Path, report: LinkReport) -> None:
        if not parked.exists():
            return
        if parked.name.endswith(REPLACE_SUFFIX):
            suffix = REPLACE_SUFFIX
        elif parked.name.endswith(ORIGINAL_SUFFIX):
            suffix = ORIGINAL_SUFFIX
        else:
            return
        original = Path(str(parked)[: -len(suffix)])
        if original.exists() or original.is_symlink():
            _safe_unlink(parked)
            return
        try:
            parked.rename(original)
            report.replaced_originals.append(str(original))
        except OSError as exc:
            report.errors.append(f"could not restore {original.name}: {exc}")

    # ---- parking originals ---------------------------------------------

    def _park_originals(self, report: LinkReport) -> None:
        """Move conflicting game files aside with REPLACE_SUFFIX.

        Mirrors RenameNonGameBigFile + RenameCustomFiles: only non-game .big
        archives and known custom extensions are touched, so real game archives
        (the ones in `game_files`) are never moved.
        """
        for _here, _dirs, files in walk_nofollow(self._game_dir):
            for path in files:
                if path.is_symlink():
                    continue
                if path.name.endswith(REPLACE_SUFFIX) or path.name.endswith(ORIGINAL_SUFFIX):
                    continue
                if "GLM" in path.relative_to(self._game_dir).parts:
                    continue

                ext = path.suffix.casefold()
                is_non_game_big = (
                    ext == ".big"
                    and path.name.casefold() not in self._game_files
                    and STEAM_COMMON_SUBDIR not in path.relative_to(self._game_dir).parts
                )
                is_custom = ext in CUSTOM_FILE_EXTENSIONS
                if not (is_non_game_big or is_custom):
                    continue

                parked = path.with_name(path.name + REPLACE_SUFFIX)
                try:
                    if parked.exists():
                        parked.unlink()
                    path.rename(parked)
                    report.replaced_originals.append(str(parked))
                except OSError as exc:
                    report.errors.append(f"could not park {path.name}: {exc}")

    def _restore_base_scripts(self, report: LinkReport) -> None:
        """No mod selected: put the stock script files back.

        Writes paths both at the game root and under the Steam common subfolder,
        because Zero Hour reads them from the latter.
        """
        targets = list(BASE_SCRIPT_FILES)
        targets += [str(Path(STEAM_COMMON_SUBDIR) / rel) for rel in BASE_SCRIPT_FILES]

        for rel in targets:
            parked = self._game_dir / f"{rel}{REPLACE_SUFFIX}"
            if parked.is_file():
                self._restore_parked(parked, report)

    # ---- creating links ------------------------------------------------

    def _link_folder(
        self,
        source: Path,
        mv: ModificationVersion,
        report: LinkReport,
        records: list[LinkRecord],
        link_empty_bigs: bool,
    ) -> None:
        """Mirror one installed version into the game folder.

        `link_empty_bigs` is true only for the main mod at the top level,
        and forced to false for anything nested below.
        """
        exceptions = {name.casefold() for name in (mv.exception_names or [])}
        extras = {name.casefold() for name in (mv.additional_file_names or [])}
        links_files = mv.mod_type is not ModType.EXECUTABLE

        for here, dirs, files in walk_nofollow(source):
            top_level = here == source
            # Only propagate link_empty_bigs at the top level.
            allow_empty = link_empty_bigs if top_level else False

            for src in dirs:
                relative = src.relative_to(source)
                if exceptions and any(p.casefold() in exceptions for p in relative.parts):
                    continue
                self._ensure_dir(src, self._game_dir / relative, report, records)

            for src in files:
                relative = src.relative_to(source)
                if exceptions and any(p.casefold() in exceptions for p in relative.parts):
                    continue
                if links_files and src.suffix.casefold() in MOD_EXCLUDED_EXTENSIONS:
                    report.skipped.append(f"{relative} ({src.suffix} not linked from a mod)")
                    continue
                if extras and relative.name.casefold() in extras:
                    continue
                self._ensure_file(
                    src,
                    relative,
                    report,
                    records,
                    allow_empty=allow_empty,
                )

    def _ensure_dir(
        self, src: Path, target: Path, report: LinkReport, records: list[LinkRecord]
    ) -> None:
        if target.is_symlink() or target.is_dir():
            return
        if target.exists():
            report.errors.append(f"cannot link {target.name}: a real file is in the way")
            return

        record = LinkRecord(
            kind="dir", target=str(target), source=str(src), strategy=self.strategy.value
        )
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            if self.strategy is LinkStrategy.SYMLINK:
                # Points at the mod's directory.
                os.symlink(_relative_or_absolute(src, target), target, target_is_directory=True)
            else:
                target.mkdir(parents=True, exist_ok=True)
            records.append(record)
        except OSError as exc:
            report.errors.append(f"could not link dir {target.name}: {exc}")

    def _ensure_file(
        self,
        src: Path,
        relative: Path,
        report: LinkReport,
        records: list[LinkRecord],
        allow_empty: bool,
    ) -> None:
        """Mirror one file, applying BIG / non-BIG linking rules.

        `.gib` files (the installed variant of `.big`) are treated as BIG archives
        and deployed with a `.big` extension so the game can find them
        (Rewritten BigFileVariantPath.GetDeploymentPath).
        """
        if src.name.endswith(ORIGINAL_SUFFIX):
            return

        if is_big_archive(src) or is_gib_file(src):
            self._mirror_big(src, relative, report, records, allow_empty)
        else:
            self._mirror_non_big(src, relative, report, records)

    def _mirror_big(
        self,
        src: Path,
        relative: Path,
        report: LinkReport,
        records: list[LinkRecord],
        allow_empty: bool,
    ) -> None:
        # Target always gets a .big extension.
        target = self._game_dir / big_target_name(str(relative))
        parked: Path | None = None

        if self._escapes_store(target):
            report.skipped.append(f"{relative} (already reachable through a directory link)")
            return

        if target.exists() and not target.is_symlink():
            parked = self._park(target, report)
        elif target.is_symlink():
            self._remove(target, report)

        if not allow_empty and is_empty_big(src):
            # A 24-byte placeholder big means "leave the game's own archive alone".
            #
            # An empty placeholder means "leave the game's own archive alone".
            # Restore the parked file rather than leaving the game with no archive.
            if parked is not None:
                self._restore_parked(parked, report)
            else:
                self._ensure_original_present(target, report)
            report.skipped.append(
                f"{relative} (empty placeholder big; kept the game's own archive)"
            )
            return

        # A .GLR source means the author picked the name deliberately, so link it
        # over the archive it was named after.
        if src.name.endswith(REPLACE_SUFFIX):
            target = self._game_dir / big_target_name(strip_parked_suffix(str(relative)))

        self._create_file_link(src, target, report, records, replaced=parked)

    def _mirror_non_big(
        self, src: Path, relative: Path, report: LinkReport, records: list[LinkRecord]
    ) -> None:
        target = self._game_dir / relative
        if self._escapes_store(target):
            report.skipped.append(f"{relative} (already reachable through a directory link)")
            return

        parked: Path | None = None
        if target.is_symlink():
            self._remove(target, report)
        elif target.is_file():
            parked = self._park(target, report)

        self._create_file_link(src, target, report, records, replaced=parked)

    def _create_file_link(
        self,
        src: Path,
        target: Path,
        report: LinkReport,
        records: list[LinkRecord],
        replaced: Path | None = None,
    ) -> None:
        """Create one link and journal it together with anything we parked.

        Parked data and the link share a single record on purpose: two records for
        one target would unlink the restored original during cleanup.
        """
        record = LinkRecord(
            kind="file",
            target=str(target),
            source=str(src),
            strategy=self.strategy.value,
            replaced=str(replaced) if replaced else "",
        )
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            if self.strategy is LinkStrategy.SYMLINK:
                os.symlink(_relative_or_absolute(src, target), target)
            elif self.strategy is LinkStrategy.HARDLINK:
                os.link(src, target)
            else:
                shutil.copy2(src, target)
            records.append(record)
        except OSError as exc:
            if exc.errno == errno.EXDEV:
                report.errors.append(
                    f"cannot hardlink {target.name}: the mod store and the game are on "
                    "different filesystems. Use --strategy copy instead."
                )
            else:
                report.errors.append(f"could not link {target.name}: {exc}")
            # Put the original back immediately; a half-applied link is worse.
            if replaced is not None:
                self._restore_parked(replaced, report)

    # ---- parking helpers ------------------------------------------------

    def _escapes_store(self, target: Path) -> bool:
        """True if `target`'s *directory* resolves into the mod store.

        Once `game/Data -> store/Mod/1.0/Data` exists, every deeper path is already
        served by that link, and linking or parking there would rewrite the source.

        Only the parent is resolved: `target` itself is often a symlink we created
        on an earlier pass (mod, then patch), and that one must be replaceable.
        """
        try:
            resolved = target.parent.resolve(strict=False)
        except OSError:
            return True
        store = self._mods_root.resolve()
        return resolved == store or store in resolved.parents

    def _ensure_original_present(self, target: Path, report: LinkReport) -> None:
        """Put the game's own file back at `target`, from `target.GLR` if needed.

        `_park_originals` may already have moved it aside before we got here.
        """
        if target.exists() or target.is_symlink():
            return
        parked = target.with_name(target.name + REPLACE_SUFFIX)
        if parked.is_file():
            self._restore_parked(parked, report)

    def _park(self, target: Path, report: LinkReport) -> Path | None:
        """Move a real game file aside with REPLACE_SUFFIX; return the parked path."""
        parked = target.with_name(target.name + REPLACE_SUFFIX)
        try:
            if parked.exists():
                parked.unlink()
            target.rename(parked)
            report.replaced_originals.append(str(parked))
            return parked
        except OSError as exc:
            report.errors.append(f"refusing to overwrite {target.name} ({exc})")
            return None

    def _remove(self, target: Path, report: LinkReport) -> None:
        try:
            if target.is_symlink() or target.is_file():
                target.unlink()
            else:
                shutil.rmtree(target, ignore_errors=True)
        except OSError as exc:
            report.errors.append(f"could not remove stale {target.name}: {exc}")

    # ---- diagnostics ----------------------------------------------------

    def snapshot(self) -> dict[str, str]:
        """Describe the whole game folder: 'link -> x', 'dir', or 'file:<size>'.

        Tests compare this before and after a link/unlink cycle to prove the
        folder is restored exactly.
        """
        out: dict[str, str] = {}
        for here, dirs, files in walk_nofollow(self._game_dir):
            for d in dirs:
                path = here / d.name
                relative = str(path.relative_to(self._game_dir))
                out[relative] = (
                    "link -> " + os.readlink(path) if path.is_symlink() else "dir"
                )
            for f in files:
                path = here / f.name
                relative = str(path.relative_to(self._game_dir))
                if path.is_symlink():
                    out[relative] = "link -> " + os.readlink(path)
                else:
                    try:
                        out[relative] = f"file:{path.stat().st_size}"
                    except OSError:
                        out[relative] = "file:?"
        return out


def _looks_like_our_copy(record: LinkRecord, target: Path) -> bool:
    """For the copy strategy, check the file is still the one we wrote.

    Only a size comparison: hashing every file on cleanup would be slow, and the
    game directory is not writable by anything else while a session is active.
    If the source is gone we assume it is ours and remove it.
    """
    if not record.source:
        return True
    source = Path(record.source)
    try:
        return target.stat().st_size == source.stat().st_size
    except OSError:
        return True


def _safe_unlink(path: Path) -> None:
    try:
        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
    except OSError:
        pass


def _relative_or_absolute(src: Path, target: Path) -> str:
    """Prefer a relative symlink so the store and game folder stay relocatable."""
    try:
        return os.path.relpath(src, target.parent)
    except ValueError:
        return str(src)