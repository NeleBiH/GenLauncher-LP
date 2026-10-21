"""Catalog fetching and parsing.

Two levels: the root manifest (which mods exist) and per-mod manifests (one URL each,
which is why N+1 HTTP requests are needed). Results are cached on disk with a
TTL so we do not hammer GitHub; on any network failure we fall back to the last good
cache instead of erroring out.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import httpx
import yaml

from .link_overrides import LinkOverrides
from .models import Catalog, ModificationVersion, ModType, RawModEntry

log = logging.getLogger(__name__)

ROOT_CATALOG_URLS = {
    "zh": "https://raw.githubusercontent.com/p0ls3r/GenLauncherModsData/master/ReposModificationDataZH4.yaml",
    "generals": "https://raw.githubusercontent.com/p0ls3r/GenLauncherModsData/master/ReposModificationDataGenerals3.yaml",
}

USER_AGENT = "GenLauncherLinux/0.1 (+https://github.com/p0ls3r/GenLauncher)"


class CatalogError(RuntimeError):
    pass


@dataclass
class _CacheEntry:
    path: Path
    fetched_at: float


@dataclass
class CatalogLoadResult:
    """What happened during a load, so the UI can warn without failing.

    Tracks partial failures per entry type (Rewritten RemoteChildManifestLoadResult
    pattern) so the UI can show "3 of 120 patches failed" rather than either
    hiding the issue or blocking on it.
    """

    catalog: Catalog
    from_cache: bool = False
    stale: bool = False
    warnings: list[str] = field(default_factory=list)
    # Catalog entries whose per-mod manifest could not be fetched.
    failed_mods: list[str] = field(default_factory=list)
    failed_patches: int = 0
    failed_addons: int = 0
    failed_executables: int = 0

    @property
    def total_failures(self) -> int:
        return len(self.failed_mods) + self.failed_patches + self.failed_addons + self.failed_executables

    @property
    def fully_loaded(self) -> bool:
        """True when every manifest loaded without error."""
        return self.total_failures == 0


class CatalogClient:
    def __init__(
        self,
        cache_dir: Path,
        ttl: int = 900,
        timeout: float = 20.0,
        transport: httpx.BaseTransport | None = None,
        link_overrides: "LinkOverrides | None" = None,
    ) -> None:
        self._cache_dir = Path(cache_dir)
        self._ttl = ttl
        self._timeout = timeout
        self._transport = transport
        self.link_overrides = link_overrides
        self._client: httpx.Client | None = None
        self._warnings: list[str] = []

    def __enter__(self) -> "CatalogClient":
        self._client = httpx.Client(
            timeout=self._timeout,
            follow_redirects=True,
            transport=self._transport,
            headers={"User-Agent": USER_AGENT},
        )
        return self

    def __exit__(self, *exc) -> None:
        if self._client:
            self._client.close()
            self._client = None

    @property
    def _http(self) -> httpx.Client:
        if self._client is None:
            self.__enter__()
        assert self._client is not None
        return self._client

    # ---- low level ------------------------------------------------------

    def _cache_path(self, url: str) -> Path:
        """Stable, collision-resistant cache filename for a URL."""
        import hashlib

        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:20]
        tail = url.rsplit("/", 1)[-1].replace("/", "_")[:40]
        safe = "".join(c for c in tail if c.isalnum() or c in "._-")
        return self._cache_dir / f"{safe or 'manifest'}.{digest}.yaml"

    def _read_cache(self, url: str) -> tuple[Any, float] | None:
        path = self._cache_path(url)
        try:
            stat = path.stat()
        except OSError:
            return None
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            return None
        return data, stat.st_mtime

    def _write_cache(self, url: str, text: str) -> None:
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._cache_path(url).write_text(text, encoding="utf-8")

    def fetch_yaml(self, url: str, use_cache: bool = True) -> tuple[Any, bool]:
        """Return (parsed, from_cache). Falls back to a stale cache on failure."""
        cached = self._read_cache(url) if use_cache else None
        fresh = cached and (time.time() - cached[1]) < self._ttl

        if fresh:
            return cached[0], True

        if url:
            try:
                response = self._http.get(url)
                response.raise_for_status()
                text = response.text
                data = yaml.safe_load(text)
                if data is None:
                    raise CatalogError(f"empty response from {url}")
                self._write_cache(url, text)
                return data, False
            except (httpx.HTTPError, yaml.YAMLError, CatalogError, OSError) as exc:
                short = str(exc).split("\nFor more")[0]
                message = f"could not fetch {url}: {short}"
                log.debug(message)
                self._warnings.append(message)
                if cached is not None:
                    return cached[0], True
                raise CatalogError(message) from exc

        if cached is not None:
            return cached[0], True
        raise CatalogError(f"no data available for {url}")

    @property
    def warnings(self) -> list[str]:
        return list(self._warnings)

    # ---- public API -----------------------------------------------------

    def load_root(self, game: str = "zh") -> CatalogLoadResult:
        url = ROOT_CATALOG_URLS.get(game)
        if not url:
            raise CatalogError(f"unknown game: {game!r} (expected one of {sorted(ROOT_CATALOG_URLS)})")
        data, from_cache = self.fetch_yaml(url)
        catalog = Catalog.from_dict(data)
        stale = from_cache and any(url in w for w in self._warnings)
        return CatalogLoadResult(
            catalog=catalog, from_cache=from_cache, stale=stale, warnings=list(self._warnings)
        )

    def load_mod_manifest(self, url: str) -> ModificationVersion:
        data, _ = self.fetch_yaml(url)
        if not isinstance(data, dict):
            raise CatalogError(f"mod manifest at {url} is not a mapping")
        return ModificationVersion.from_dict(data)

    def load_many(
        self, urls: Iterable[str], default_type: ModType | None = None,
    ) -> tuple[list[ModificationVersion], int]:
        """Fetch several manifests, skipping (and reporting) the ones that fail.

        Returns (successful_versions, failed_count). One dead patch link must
        not take the whole launcher down.
        """
        out: list[ModificationVersion] = []
        failed = 0
        for url in urls:
            if not url:
                continue
            try:
                mv = self.load_mod_manifest(url)
                if default_type and mv.mod_type is ModType.MOD:
                    mv.mod_type = default_type
                out.append(mv)
            except CatalogError as exc:
                log.debug("skipping manifest %s: %s", url, str(exc).split("\nFor more")[0])
                failed += 1
        return out, failed

    def load_everything(
        self, game: str = "zh", want_installed_only: set[str] | None = None
    ) -> tuple[
        CatalogLoadResult,
        dict[str, ModificationVersion],
        list[ModificationVersion],
        list[ModificationVersion],
        list[ModificationVersion],
    ]:
        """Root + every mod manifest + patches/addons/exes.

        Returns (root_result, mods_by_name, patches, addons, executables).

        Patches/addons are fetched for all mods because they are shown in
        the list before a mod is selected; `want_installed_only` narrows that when
        the caller only cares about what is on disk.
        """
        root = self.load_root(game)
        catalog = root.catalog

        mod_manifests: list[str] = [
            e.mod_link for e in catalog.mod_datas if e.mod_link
        ]
        if want_installed_only:
            allowed = {n.casefold() for n in want_installed_only}
            mod_manifests = [
                e.mod_link for e in catalog.mod_datas if e.mod_link and e.mod_name.casefold() in allowed
            ]

        mods: dict[str, ModificationVersion] = {}
        failed: list[str] = []
        for entry in catalog.mod_datas:
            if entry.mod_link not in mod_manifests:
                continue
            try:
                mv = self.load_mod_manifest(entry.mod_link)
                if not mv.mod_type:
                    mv.mod_type = ModType.MOD
                # DependenceName is empty for mods; keep the catalog's name as the
                # authoritative label since the two can differ in case.
                mv.name = mv.name or entry.mod_name
                mods[entry.mod_name.casefold()] = mv
            except CatalogError:
                failed.append(entry.mod_name)

        # Patches and addons. For installed mods resolve the real DependenceName
        # from the manifest, otherwise fall back to the catalog's spelling.
        patches: list[ModificationVersion] = []
        addons: list[ModificationVersion] = []
        targets: list[RawModEntry] = catalog.mod_datas
        if want_installed_only:
            allowed = {n.casefold() for n in want_installed_only}
            targets = [e for e in targets if e.mod_name.casefold() in allowed]

        patch_failures = 0
        addon_failures = 0
        for entry in targets:
            manifest = mods.get(entry.mod_name.casefold())
            dep = (manifest.dependence_name if manifest else "") or entry.mod_name
            for url in entry.mod_patches:
                loaded, n_fail = self.load_many([url], ModType.PATCH)
                patch_failures += n_fail
                for mv in loaded:
                    mv.dependence_name = mv.dependence_name or dep
                    patches.append(mv)
            for url in entry.mod_addons:
                loaded, n_fail = self.load_many([url], ModType.ADDON)
                addon_failures += n_fail
                for mv in loaded:
                    mv.dependence_name = mv.dependence_name or dep
                    addons.append(mv)

        # Base-game patches/addons, plus every executable.
        for url in catalog.original_game_patches:
            loaded, n_fail = self.load_many([url], ModType.PATCH)
            patch_failures += n_fail
            for mv in loaded:
                mv.dependence_name = mv.dependence_name or "Original Game"
                patches.append(mv)
        for url in catalog.original_game_addons:
            loaded, n_fail = self.load_many([url], ModType.ADDON)
            addon_failures += n_fail
            for mv in loaded:
                mv.dependence_name = mv.dependence_name or "Original Game"
                addons.append(mv)

        executables, exe_failures = self.load_executables(catalog)

        # User-supplied links win over the catalog's, for every entry type.
        if self.link_overrides is not None:
            groups = (
                list(mods.values()),
                patches,
                addons,
                executables,
            )
            hits = sum(self.link_overrides.apply_all(g) for g in groups)
            if hits:
                root.warnings.append(
                    f"using your download link for {hits} "
                    f"catalog {'entry' if hits == 1 else 'entries'}"
                )

        root.failed_mods = failed
        root.failed_patches = patch_failures
        root.failed_addons = addon_failures
        root.failed_executables = exe_failures
        if failed:
            root.warnings.append(
                f"could not load manifest for: {', '.join(sorted(failed))}"
            )
        if patch_failures or addon_failures or exe_failures:
            parts = []
            if patch_failures:
                parts.append(f"{patch_failures} patch{'es' if patch_failures != 1 else ''}")
            if addon_failures:
                parts.append(f"{addon_failures} addon{'s' if addon_failures != 1 else ''}")
            if exe_failures:
                parts.append(f"{exe_failures} executable{'s' if exe_failures != 1 else ''}")
            root.warnings.append(f"failed to load {', '.join(parts)} manifests")

        return root, mods, patches, addons, executables

    def load_executables(self, catalog: Catalog) -> tuple[list[ModificationVersion], int]:
        exes: list[ModificationVersion] = []
        exe_failures = 0
        for entry in catalog.executables:
            loaded, n_fail = self.load_many([entry.mod_link], ModType.EXECUTABLE)
            exe_failures += n_fail
            for mv in loaded:
                mv.dependence_name = entry.dependence_name or mv.dependence_name
                exes.append(mv)
        return exes, exe_failures