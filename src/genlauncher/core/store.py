"""Local registry of installed modifications.

JSON is enough for v1. The store is the source of truth for "what is installed",
and it also mirrors what is physically on disk so we can detect drift.
"""

from __future__ import annotations

import json
import logging
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from .config import ADDONS_DIR, EXECUTABLES_DIR, PATCHES_DIR
from .models import ORIGINAL_GAME_ALIAS, ModificationVersion, ModType

log = logging.getLogger(__name__)


def _safe_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


@dataclass
class InstalledMod:
    """One modification with all its known versions."""

    name: str
    mod_type: ModType = ModType.MOD
    dependence_name: str = ""
    versions: dict[str, ModificationVersion] = field(default_factory=dict)
    manual: bool = False

    @property
    def key(self) -> str:
        return self.name.casefold()

    def sorted_versions(self) -> list[ModificationVersion]:
        return sorted(self.versions.values(), key=lambda v: v.sort_key)

    @property
    def latest(self) -> ModificationVersion | None:
        ordered = self.sorted_versions()
        return ordered[-1] if ordered else None

    def has_update(self, catalog_version: ModificationVersion | None) -> bool:
        """True when the catalog offers something newer than what we have."""
        if catalog_version is None:
            return False
        latest = self.latest
        if latest is None:
            return False
        return catalog_version.sort_key > latest.sort_key

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "mod_type": self.mod_type.value,
            "dependence_name": self.dependence_name,
            "manual": self.manual,
            "versions": [v.to_dict() for v in self.sorted_versions()],
        }

    @classmethod
    def from_dict(cls, raw: dict) -> "InstalledMod":
        versions: dict[str, ModificationVersion] = {}
        for entry in raw.get("versions", []) or []:
            mv = ModificationVersion.from_dict(entry, ModType.MOD)
            mv.mod_type = ModType.parse(raw.get("mod_type", "Mod"), ModType.MOD)
            mv.dependence_name = mv.dependence_name or raw.get("dependence_name", "")
            mv.installed = True
            mv.manual = bool(raw.get("manual", False))
            versions[mv.version] = mv
        return cls(
            name=str(raw.get("name", "")),
            mod_type=ModType.parse(raw.get("mod_type", "Mod"), ModType.MOD),
            dependence_name=str(raw.get("dependence_name", "")),
            versions=versions,
            manual=bool(raw.get("manual", False)),
        )


class ModStore:
    """Installed-mod registry backed by a JSON file."""

    def __init__(self, data_dir: Path) -> None:
        self._dir = Path(data_dir)
        self._file = self._dir / "store.json"
        self._mods: dict[str, InstalledMod] = {}
        self._load()

    # ---- persistence ----------------------------------------------------

    def _load(self) -> None:
        try:
            raw = json.loads(self._file.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return
        for entry in raw.get("mods", []) or []:
            im = InstalledMod.from_dict(entry)
            if im.name:
                self._mods[im.key] = im

    def save(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "mods": [m.to_dict() for m in sorted(self._mods.values(), key=lambda m: m.key)],
        }
        tmp = self._file.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(self._file)

    # ---- queries --------------------------------------------------------

    def all(self) -> list[InstalledMod]:
        return sorted(self._mods.values(), key=lambda m: m.name.casefold())

    def by_type(self, mod_type: ModType) -> list[InstalledMod]:
        return [m for m in self.all() if m.mod_type is mod_type]

    def get(self, name: str, mod_type: ModType | None = None) -> InstalledMod | None:
        found = self._mods.get(name.casefold())
        if found and (mod_type is None or found.mod_type is mod_type):
            return found
        return None

    def find_version(self, name: str, version: str) -> ModificationVersion | None:
        found = self.get(name)
        return found.versions.get(version) if found else None

    def is_installed(self, mv: ModificationVersion) -> bool:
        found = self.find_version(mv.name, mv.version)
        return bool(found and found.installed)

    def mods_with_updates(self, catalog_mods: dict[str, ModificationVersion]) -> list[str]:
        """Names of installed mods for which the catalog offers something newer.

        The catalog is keyed by the name as written in the YAML, which does not
        always match our casefolded key, so look up case-insensitively.
        """
        lookup = {name.casefold(): mv for name, mv in catalog_mods.items()}
        out = []
        for im in self.all():
            candidate = lookup.get(im.key)
            if candidate and im.has_update(candidate):
                out.append(im.name)
        return out

    # ---- mutation -------------------------------------------------------

    def upsert(self, mv: ModificationVersion, mods_root: Path) -> None:
        """Record a version as installed, creating the container if needed."""
        mv.installed = True
        mv.installed_at = mv.installed_at or time.time()
        dep = mv.dependence_name or ORIGINAL_GAME_ALIAS
        key = mv.name.casefold()
        existing = self._mods.get(key)
        if existing is None:
            existing = InstalledMod(
                name=mv.name, mod_type=mv.mod_type, dependence_name=dep, manual=mv.manual
            )
            self._mods[key] = existing
        existing.mod_type = mv.mod_type
        existing.dependence_name = dep
        existing.versions[mv.version] = mv
        self.save()

    def register_manual(self, name: str, folder: Path, mods_root: Path, version: str = "manual") -> InstalledMod:
        """Track a folder the user pointed us at, without a catalog entry."""
        mv = ModificationVersion(
            name=name,
            version=version,
            mod_type=ModType.MOD,
            dependence_name="",
            installed=True,
            installed_at=time.time(),
            manual=True,
        )
        im = InstalledMod(
            name=name,
            mod_type=ModType.MOD,
            manual=True,
            versions={version: mv},
        )
        self._mods[im.key] = im
        self.save()
        return im

    def remove_version(self, name: str, version: str, mods_root: Path, delete_files: bool = True) -> bool:
        im = self.get(name)
        if im is None:
            return False
        mv = im.versions.pop(version, None)
        if mv is None:
            return False
        if delete_files:
            folder = mv.folder_name(mods_root)
            if folder.is_dir():
                shutil.rmtree(folder, ignore_errors=True)
                prune_empty_parents(folder.parent, mods_root)
        if not im.versions:
            self._mods.pop(im.key, None)
        self.save()
        return True

    def remove_mod(self, name: str, mods_root: Path, delete_files: bool = True) -> bool:
        im = self.get(name)
        if im is None:
            return False
        if delete_files:
            for mv in list(im.versions.values()):
                folder = mv.folder_name(mods_root)
                if folder.is_dir():
                    shutil.rmtree(folder, ignore_errors=True)
                    prune_empty_parents(folder.parent, mods_root)
        self._mods.pop(im.key, None)
        self.save()
        return True

    def reconcile_with_disk(self, mods_root: Path) -> list[str]:
        """Drop registry entries whose folders vanished, and adopt orphans.

        Disk-based scan, so a user who deletes folders by hand
        does not end up with phantom "installed" entries.
        """
        changed: list[str] = []
        mods_root = Path(mods_root)
        if not mods_root.is_dir():
            return changed

        for im in list(self.all()):
            for version, mv in list(im.versions.items()):
                if not mv.folder_name(mods_root).is_dir():
                    im.versions.pop(version)
                    changed.append(f"{im.name} {version}")
            if not im.versions:
                self._mods.pop(im.key, None)

        for kind, sub in (
            (ModType.PATCH, PATCHES_DIR),
            (ModType.ADDON, ADDONS_DIR),
            (ModType.EXECUTABLE, EXECUTABLES_DIR),
        ):
            base = mods_root
            for dep_dir in sorted(p for p in base.iterdir() if p.is_dir()):
                type_dir = dep_dir / sub
                if not type_dir.is_dir():
                    continue
                for name_dir in sorted(p for p in type_dir.iterdir() if p.is_dir()):
                    im = self.get(name_dir.name, mod_type=kind)
                    if im is not None:
                        continue
                    versions: dict[str, ModificationVersion] = {}
                    for ver_dir in sorted(p for p in name_dir.iterdir() if p.is_dir()):
                        mv = ModificationVersion(
                            name=name_dir.name,
                            version=ver_dir.name,
                            mod_type=kind,
                            dependence_name=dep_dir.name,
                            installed=True,
                            installed_at=_safe_mtime(ver_dir),
                        )
                        versions[mv.version] = mv
                        changed.append(f"adopted {name_dir.name} {ver_dir.name}")
                    if versions:
                        new_im = InstalledMod(
                            name=name_dir.name,
                            mod_type=kind,
                            dependence_name=dep_dir.name,
                            versions=versions,
                        )
                        self._mods[new_im.key] = new_im

        if changed:
            self.save()
        return changed


def prune_empty_parents(folder: Path, stop_at: Path) -> None:
    """Remove now-empty Addons/Patches/Executables/dep dirs up to the store root."""
    stop_at = Path(stop_at).resolve()
    current = Path(folder).resolve()
    while current != stop_at and stop_at in current.parents:
        try:
            next(current.iterdir())
            return  # not empty, stop
        except StopIteration:
            pass
        except OSError:
            return
        parent = current.parent
        try:
            current.rmdir()
        except OSError:
            return
        current = parent