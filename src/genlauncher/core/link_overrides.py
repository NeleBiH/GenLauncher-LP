"""User-supplied download links for catalog entries whose link has died.

The bundled catalog is a remote YAML that many mods still point at OneDrive
shares which have since been revoked (the API answers "The sharing token is
invalid"). Those entries cannot be fixed in the catalog, so this keeps a small
user-owned override map keyed by mod name and version that is merged over
``simple_download_link`` at load time.

Deliberately stored outside the cache directory: the YAML cache is keyed by a
content digest and gets evicted, so an override written there would silently
disappear on the next catalog refresh.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)

OVERRIDES_FILENAME = "link_overrides.json"


class LinkOverrides:
    """Persistent mod-name -> version -> download URL overrides."""

    def __init__(self, data_dir: Path):
        self._path = Path(data_dir) / OVERRIDES_FILENAME
        self._data: dict[str, dict[str, str]] = {}
        self.load()

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> None:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            self._data = {}
            return
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("Ignoring unreadable %s: %s", self._path, exc)
            self._data = {}
            return

        clean: dict[str, dict[str, str]] = {}
        if isinstance(raw, dict):
            for mod, versions in raw.items():
                if isinstance(mod, str) and isinstance(versions, dict):
                    pairs = {
                        str(v): str(u)
                        for v, u in versions.items()
                        if isinstance(v, str) and isinstance(u, str) and u.strip()
                    }
                    if pairs:
                        clean[mod.casefold()] = pairs
        self._data = clean

    def save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._data, indent=2, sort_keys=True), encoding="utf-8")
            tmp.replace(self._path)
        except OSError as exc:
            log.error("Failed to save link overrides: %s", exc)

    def get(self, mod_name: str, version: str) -> str:
        return self._data.get(mod_name.casefold(), {}).get(version, "")

    def set(self, mod_name: str, version: str, url: str) -> None:
        key = mod_name.casefold()
        url = (url or "").strip()
        if not url:
            self._data.get(key, {}).pop(version, None)
            if key in self._data and not self._data[key]:
                del self._data[key]
        else:
            self._data.setdefault(key, {})[version] = url
        self.save()

    def clear(self, mod_name: str, version: str) -> None:
        self.set(mod_name, version, "")

    def apply(self, mv) -> None:
        """Overwrite mv.simple_download_link when the user supplied one."""
        url = self.get(mv.name, mv.version)
        if url:
            mv.simple_download_link = url
            log.info("Using user download link for %s %s", mv.name, mv.version)

    def apply_all(self, versions) -> int:
        """Apply to an iterable of ModificationVersions; returns the hit count."""
        hits = 0
        for mv in versions:
            before = mv.simple_download_link
            self.apply(mv)
            if mv.simple_download_link != before:
                hits += 1
        return hits

    def entries(self) -> dict[str, dict[str, str]]:
        return {k: dict(v) for k, v in self._data.items()}

    def __len__(self) -> int:
        return sum(len(v) for v in self._data.values())
