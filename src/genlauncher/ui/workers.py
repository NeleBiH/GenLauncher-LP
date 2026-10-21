"""QThread workers that wrap core operations for the GUI.

Each worker emits Qt signals so the UI thread can react without blocking.
All heavy I/O (network, disk, game process) runs off the main thread.
"""

from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from PySide6.QtCore import QThread, Signal

from ..core.catalog import CatalogClient, CatalogError, CatalogLoadResult
from ..core.config import GAME_EXE_CANDIDATES, GAME_FILES, Paths, Settings
from ..core.downloader import DownloadCancelled, DownloadError, Downloader, Progress
from ..core.installer import InstallError, Installer
from ..core.linker import LinkReport, LinkStrategy, Linker
from ..core.models import ModificationVersion, ModType
from ..core import process as process_watch
from ..core.store import ModStore

log = logging.getLogger("genlauncher.ui")


class CatalogResult:
    """Bundle of everything the CatalogWorker produces."""

    __slots__ = ("load_result", "mods", "patches", "addons", "exes")

    def __init__(
        self,
        load_result: CatalogLoadResult,
        mods: dict,
        patches: list,
        addons: list,
        exes: list,
    ):
        self.load_result = load_result
        self.mods = mods
        self.patches = patches
        self.addons = addons
        self.exes = exes


class CatalogWorker(QThread):
    """Fetch the full catalog (root + all mod manifests) in the background."""

    # Single signal with all data bundled to avoid race conditions.
    finished = Signal(object)  # CatalogResult
    error = Signal(str)

    def __init__(
        self,
        cache_dir: Path,
        game: str = "zh",
        ttl: int = 900,
        link_overrides=None,
        parent=None,
    ):
        super().__init__(parent)
        self._cache_dir = cache_dir
        self._game = game
        self._ttl = ttl
        self._overrides = link_overrides

    def run(self):
        try:
            with CatalogClient(
                cache_dir=self._cache_dir,
                ttl=self._ttl,
                link_overrides=self._overrides,
            ) as client:
                result, mods, patches, addons, exes = client.load_everything(
                    self._game
                )
            self.finished.emit(CatalogResult(result, mods, patches, addons, exes))
        except CatalogError as exc:
            self.error.emit(str(exc))
        except Exception as exc:
            log.exception("catalog worker failed")
            self.error.emit(f"Unexpected error: {exc}")


class DownloadInstallWorker(QThread):
    """Download an archive and install it as a mod version."""

    progress = Signal(object)  # Progress dataclass
    status = Signal(str)       # human-readable status message
    finished = Signal(object)  # ModificationVersion (with installed=True)
    cancelled = Signal()       # user pressed STOP; distinct from failure
    error = Signal(str)

    def __init__(
        self,
        mv: ModificationVersion,
        paths: Paths,
        parent=None,
    ):
        super().__init__(parent)
        self._mv = mv
        self._paths = paths
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    @property
    def is_cancelled(self) -> bool:
        return self._cancelled

    def run(self):
        mv = self._mv
        if not mv.simple_download_link:
            self.error.emit(f"No download link for {mv.name}")
            return

        try:
            self.status.emit("Downloading...")
            with Downloader() as downloader:
                archive = downloader.download_to_cache(
                    mv.simple_download_link,
                    self._paths.cache_dir,
                    progress=lambda p: self.progress.emit(p),
                    cancel=lambda: self._cancelled,
                )

            if self._cancelled:
                self.status.emit("Cancelled")
                self.cancelled.emit()
                return

            self.status.emit("Unpacking...")
            installer = Installer(self._paths.mods_dir)
            installer.install(
                mv, archive, progress=lambda msg: self.status.emit(msg)
            )

            mv.installed = True
            store = ModStore(self._paths.data_dir)
            store.upsert(mv, self._paths.mods_dir)

            self.finished.emit(mv)

        except DownloadCancelled:
            self.status.emit("Cancelled")
            self.cancelled.emit()
        except (DownloadError, InstallError) as exc:
            self.error.emit(str(exc))
        except Exception as exc:
            log.exception("download worker failed")
            self.error.emit(f"Unexpected error: {exc}")


class S3DownloadInstallWorker(QThread):
    """Download a mod via S3 multi-file transfer and install it."""

    progress = Signal(object)  # S3Progress dataclass
    status = Signal(str)
    finished = Signal(object)  # ModificationVersion (installed=True)
    cancelled = Signal()
    error = Signal(str)

    def __init__(
        self,
        mv: ModificationVersion,
        paths: Paths,
        prev_version_dir: Path | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self._mv = mv
        self._paths = paths
        self._prev_dir = prev_version_dir
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    @property
    def is_cancelled(self) -> bool:
        return self._cancelled

    def run(self):
        from ..core.s3_downloader import (
            S3DownloadCancelled,
            S3DownloadError,
            S3Downloader,
        )

        mv = self._mv
        if not mv.uses_s3:
            self.error.emit(f"No S3 info for {mv.name}")
            return

        try:
            self.status.emit("Listing files...")
            downloader = S3Downloader()

            # S3 downloads directly into the mod folder (not an archive).
            staging_dir = mv.folder_name(self._paths.mods_dir)
            staging_dir.parent.mkdir(parents=True, exist_ok=True)

            from ..core.config import TEMP_SUFFIX
            temp_dir = staging_dir.parent / f"{staging_dir.name}{TEMP_SUFFIX}"
            if temp_dir.exists():
                import shutil
                shutil.rmtree(temp_dir, ignore_errors=True)
            temp_dir.mkdir(parents=True)

            self.status.emit("Downloading...")
            downloader.download_package(
                endpoint=mv.s3_host_link,
                bucket=mv.s3_bucket_name,
                prefix=mv.s3_folder_name,
                dest_dir=temp_dir,
                access_key=mv.s3_host_public_key,
                secret_key=mv.s3_host_secret_key,
                prev_version_dir=self._prev_dir,
                progress=lambda p: self.progress.emit(p),
                cancel=lambda: self._cancelled,
            )

            if self._cancelled:
                self.status.emit("Cancelled")
                self.cancelled.emit()
                return

            # Atomic move: temp -> final.
            self.status.emit("Installing...")
            if staging_dir.exists():
                import shutil
                shutil.rmtree(staging_dir, ignore_errors=True)
            temp_dir.rename(staging_dir)

            # Save checksums.
            try:
                from ..core.integrity import save_checksums
                self.status.emit("Computing checksums...")
                save_checksums(staging_dir)
            except Exception:
                log.debug("checksum generation failed", exc_info=True)

            mv.installed = True
            store = ModStore(self._paths.data_dir)
            store.upsert(mv, self._paths.mods_dir)

            self.finished.emit(mv)

        except S3DownloadCancelled:
            self.status.emit("Cancelled")
            self.cancelled.emit()
        except S3DownloadError as exc:
            self.error.emit(str(exc))
        except Exception as exc:
            log.exception("S3 download worker failed")
            self.error.emit(f"Unexpected error: {exc}")


class IntegrityWorker(QThread):
    """Verify installed mod files off the GUI thread.

    Hashing every file of every selected mod is multi-gigabyte of hashing for
    the larger mods; running verify_checksums inline froze the window for
    minutes before the game would even start.
    """

    # NOT named 'finished': QThread already defines that signal and
    # redeclaring it in a subclass shadows the built-in one.
    checked = Signal(object)  # IntegrityResult

    def __init__(self, folders: list[Path], parent=None):
        super().__init__(parent)
        self._folders = list(folders)

    def run(self):
        from ..core.integrity import IntegrityResult, verify_checksums

        merged = IntegrityResult()
        for folder in self._folders:
            try:
                result = verify_checksums(folder)
            except Exception as exc:
                log.error("integrity check failed for %s: %s", folder.name, exc)
                merged.ok = False
                merged.corrupted.append(f"{folder.name}: {exc}")
                continue

            prefix = folder.name
            merged.no_baseline = merged.no_baseline or result.no_baseline
            merged.ok = merged.ok and result.ok
            merged.missing += [f"{prefix}/{m}" for m in result.missing]
            merged.corrupted += [f"{prefix}/{c}" for c in result.corrupted]
            merged.extra += [f"{prefix}/{e}" for e in result.extra]
        self.checked.emit(merged)


class LinkWorker(QThread):
    """Link mod versions into the game folder."""

    finished = Signal(object)  # LinkReport
    error = Signal(str)

    def __init__(
        self,
        game_dir: Path,
        journal_path: Path,
        mods_root: Path,
        versions: list[ModificationVersion],
        strategy: LinkStrategy = LinkStrategy.SYMLINK,
        parent=None,
    ):
        super().__init__(parent)
        self._game_dir = game_dir
        self._journal_path = journal_path
        self._mods_root = mods_root
        self._versions = versions
        self._strategy = strategy

    def run(self):
        try:
            linker = Linker(
                self._game_dir,
                self._journal_path,
                self._mods_root,
                strategy=self._strategy,
                game_files=set(GAME_FILES),
            )
            report = linker.link(self._versions)
            self.finished.emit(report)
        except Exception as exc:
            log.exception("link worker failed")
            self.error.emit(str(exc))


class UnlinkWorker(QThread):
    """Remove all links and restore the game folder."""

    finished = Signal(object)  # LinkReport
    error = Signal(str)

    def __init__(
        self,
        game_dir: Path,
        journal_path: Path,
        mods_root: Path,
        strategy: LinkStrategy = LinkStrategy.SYMLINK,
        parent=None,
    ):
        super().__init__(parent)
        self._game_dir = game_dir
        self._journal_path = journal_path
        self._mods_root = mods_root
        self._strategy = strategy

    def run(self):
        try:
            linker = Linker(
                self._game_dir,
                self._journal_path,
                self._mods_root,
                strategy=self._strategy,
                game_files=set(GAME_FILES),
            )
            report = linker.unlink()
            self.finished.emit(report)
        except Exception as exc:
            log.exception("unlink worker failed")
            self.error.emit(str(exc))


class GameRunner(QThread):
    """Launch the game and wait for it to exit."""

    started_ok = Signal()
    finished = Signal(int, float)  # return code, session duration (seconds)
    output = Signal(str)           # stdout/stderr lines
    error = Signal(str)

    def __init__(
        self,
        game_dir: Path,
        settings: Settings,
        appid: str = "",
        compat_data: Path | None = None,
        exe: Path | None = None,
        args_override: list[str] | None = None,
        mode: str = "zh",
        parent=None,
    ):
        super().__init__(parent)
        self._game_dir = game_dir
        self._settings = settings
        self._appid = appid
        self._compat_data = compat_data
        # World Builder and similar tools share the game's Proton prefix but
        # have no Steam app id of their own, so they are always run via Proton.
        self._exe = exe
        self._args_override = args_override
        self._mode = mode
        # Steam mode waits for the real game process instead of the hand-off
        # helper; the window is overridable so a launch that never appears
        # cannot stall callers (and so tests stay fast).
        self.steam_start_timeout = process_watch.START_TIMEOUT

    def _steam_command(self, args: list[str]) -> list[str] | None:
        from ..core import steam as steam_mod

        steam_exe = steam_mod.steam_executable()
        if not steam_exe:
            return None

        # Settings.appid is only an override; the detected appid is the truth.
        appid = self._settings.appid or self._appid
        if not appid:
            return None

        return [steam_exe, "-silent", "-applaunch", appid, *args]

    def _proton_command(self, exe: Path, args: list[str]) -> tuple[list[str], dict[str, str]] | None:
        from ..core import steam as steam_mod

        builds = steam_mod.list_proton_builds()
        chosen: steam_mod.ProtonBuild | None = None
        wanted = self._settings.proton.strip().casefold()
        if wanted:
            chosen = next((b for b in builds if b.name.casefold() == wanted), None)
            if chosen is None:
                return None
        else:
            # Prefer a recent GE-Proton, then official Proton, then anything else.
            def rank(build: steam_mod.ProtonBuild) -> tuple:
                n = build.name.casefold()
                ge = n.startswith("ge-proton")
                digits = "".join(c for c in n if c.isdigit())
                return (0 if ge else 1, -(int(digits) if digits else 0), n)

            usable = [b for b in builds if b.is_valid()]
            chosen = sorted(usable, key=rank)[0] if usable else None

        if chosen is None or not chosen.is_valid():
            return None

        cmd = [str(chosen.proton_script), "run", str(exe), *args]
        env = {
            "STEAM_COMPAT_DATA_PATH": str(self._compat_data) if self._compat_data else "",
            "STEAM_COMPAT_CLIENT_INSTALL_PATH": str(self._game_dir),
        }
        return cmd, env

    def run(self):
        from ..core import steam as steam_mod

        s = self._settings
        exe = self._exe or steam_mod.resolve_game_exe(self._game_dir, self._mode)
        if exe is None:
            self.error.emit("Cannot find game executable")
            return

        if self._args_override is not None:
            args = list(self._args_override)
        else:
            args = []
            if s.windowed:
                args.append("-win")
            if s.quickstart:
                args.extend(["-quickstart", "-noshellmap"])
            if s.extra_args:
                args.extend(s.extra_args.split())

        env = None
        # An explicit exe means a bundled tool, which Steam cannot launch by
        # appid; go straight to Proton inside the existing prefix.
        use_steam = self._exe is None and s.launch_mode == "steam"
        if use_steam:
            cmd = self._steam_command(args)
            if cmd is None:
                if not self._settings.appid and not self._appid:
                    self.error.emit(
                        "Cannot launch: no Steam app id known for this install.\n\n"
                        "Set it in Options, or switch launch mode to Proton."
                    )
                else:
                    self.error.emit("Cannot find the Steam client executable")
                return
        else:
            result = self._proton_command(exe, args)
            if result is None:
                self.error.emit(
                    "Cannot find a usable Proton build.\n"
                    "Install one via Steam, or switch launch mode to Steam."
                )
                return
            cmd, env = result

        # DXVK (Vulkan) is Proton's default.  When the user disables it we
        # force the wined3d (OpenGL) backend instead.
        merged_env = dict(os.environ)
        if env:
            merged_env.update({k: v for k, v in env.items() if v})
        if not s.use_vulkan:
            merged_env["PROTON_USE_WINED3D"] = "1"

        try:
            log.info("launching: %s", " ".join(cmd))
            proc = subprocess.Popen(
                cmd,
                cwd=str(self._game_dir),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                env=merged_env,
            )
        except OSError as exc:
            self.error.emit(f"Cannot launch: {exc}")
            return

        # Only claim success once the process actually exists.
        self.started_ok.emit()
        t0 = time.monotonic()

        if use_steam:
            # Steam hands the launch to the running client and returns at once,
            # so its exit says nothing about the game. Watch the game itself,
            # otherwise we unlink and hand the vanilla archives back while it
            # is still loading.
            self._drain_output_async(proc)
            rc = self._track_steam_session(proc, exe)
            self.finished.emit(rc, time.monotonic() - t0)
            return

        if proc.stdout:
            for line in proc.stdout:
                self.output.emit(line.rstrip())
        rc = proc.wait()
        self.finished.emit(rc, time.monotonic() - t0)

    def _drain_output_async(self, proc: subprocess.Popen) -> None:
        """Keep pumping a child's stdout without blocking the session watcher."""
        stream = proc.stdout
        if stream is None:
            return

        def _pump() -> None:
            try:
                for line in stream:
                    self.output.emit(line.rstrip())
            except (OSError, ValueError):  # pragma: no cover - stream closed early
                pass

        threading.Thread(target=_pump, daemon=True, name="steam-output").start()

    def _track_steam_session(self, proc: subprocess.Popen, exe: Path | None) -> int:
        """Wait for the game to start and finish, then reap the Steam helper.

        Returns Steam's exit code, which is only meaningful when the launch
        itself failed; a running session is reported by the watcher instead.
        """
        names = process_watch.normalize_names(
            [*GAME_EXE_CANDIDATES, exe.name if exe else ""]
        )
        log.info("watching for game process: %s", ", ".join(sorted(names)))

        observed = process_watch.wait_for_session(
            names, start_timeout=self.steam_start_timeout
        )
        if observed:
            log.info("game process exited; the game folder can be restored")
        elif proc.poll() not in (None, 0):
            log.warning("Steam launch failed with exit code %s", proc.returncode)
        else:
            log.warning(
                "no %s process appeared within %.0fs; falling back to Steam's "
                "exit status. The game folder may be restored while the game "
                "is still running.",
                ", ".join(sorted(names)),
                self.steam_start_timeout,
            )

        try:
            return proc.wait(timeout=30)
        except subprocess.TimeoutExpired:  # pragma: no cover - helper should exit
            log.warning("Steam hand-off process did not exit; not waiting for it")
            return 0


class PatchCheckWorker(QThread):
    """Check for the latest community patch version in the background."""

    result = Signal(str)   # latest tag, or "" on error

    def __init__(self, game: str = "zh", parent=None):
        super().__init__(parent)
        self._game = game

    def run(self):
        try:
            from ..core.patcher import fetch_latest_release
            release = fetch_latest_release(self._game)
            self.result.emit(release.tag)
        except Exception:
            self.result.emit("")


class GamePatchWorker(QThread):
    """Download and install the latest TheSuperHackers community patch."""

    status = Signal(str)       # human-readable status message
    progress = Signal(object)  # Progress dataclass
    finished = Signal(str)     # tag of installed version
    error = Signal(str)

    def __init__(
        self,
        game_dir: Path,
        paths: Paths,
        game: str = "zh",
        parent=None,
    ):
        super().__init__(parent)
        self._game_dir = game_dir
        self._paths = paths
        self._game = game
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        from ..core import patcher

        try:
            self.status.emit("Checking for latest version...")
            release = patcher.fetch_latest_release(self._game)

            self.status.emit(f"Downloading {release.asset_name}...")
            archive = patcher.download_release(
                release,
                self._paths.cache_dir,
                progress=lambda p: self.progress.emit(p),
                cancel=lambda: self._cancelled,
            )

            if self._cancelled:
                self.status.emit("Cancelled")
                return

            self.status.emit("Installing patch...")
            patcher.install_patch(archive, self._game_dir, self._game)

            self.finished.emit(release.tag)

        except patcher.PatchError as exc:
            self.error.emit(str(exc))
        except Exception as exc:
            log.exception("patch worker failed")
            self.error.emit(f"Unexpected error: {exc}")


class ImageLoader(QThread):
    """Download mod preview images in the background.

    The catalog hosts most previews on imgur/moddb, which rate-limit bursts with
    HTTP 429. So requests are spaced out, 429/5xx are retried with backoff, and a
    failure is remembered in a ``.missing`` marker so we stop asking for it.

    Emits ``image_ready(mod_name, data)`` for each image and ``image_missing(name)``
    for each one that could not be fetched, so the UI can show a placeholder.
    """

    image_ready = Signal(str, bytes)  # (mod_name, raw_bytes)
    image_missing = Signal(str)       # (mod_name,)
    finished = Signal()

    #: imgur starts answering 429 after a handful of rapid requests.
    MIN_INTERVAL = 0.8
    MAX_RETRIES = 3
    BACKOFF = 3.0

    _MAGIC = (
        (b"\x89PNG\r\n\x1a\n", ".png"),
        (b"\xff\xd8\xff", ".jpg"),
        (b"GIF87a", ".gif"),
        (b"GIF89a", ".gif"),
        (b"BM", ".bmp"),
        (b"RIFF", ".webp"),
    )

    def __init__(
        self,
        requests: list[tuple[str, str]],  # [(mod_name, image_url), ...]
        cache_dir: Path,
        parent=None,
    ):
        super().__init__(parent)
        self._requests = requests
        self._cache_dir = cache_dir / "images"

    @classmethod
    def _sniff(cls, data: bytes) -> str | None:
        """Return the extension implied by the leading bytes, or None."""
        for magic, ext in cls._MAGIC:
            if data.startswith(magic):
                return ext
        # RIFF is a container; only WebP puts WEBP there.
        if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            return ".webp"
        return None

    def _cached(self, safe_name: str) -> Path | None:
        """Find a previously downloaded image for this mod, if any."""
        for hit in sorted(self._cache_dir.glob(f"{safe_name}.*")):
            if hit.suffix.lower() in (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"):
                return hit
        return None

    def _missing_marker(self, safe_name: str) -> Path:
        return self._cache_dir / f"{safe_name}.missing"

    def run(self):
        import httpx

        self._cache_dir.mkdir(parents=True, exist_ok=True)
        last_request = 0.0

        with httpx.Client(
            timeout=20,
            follow_redirects=True,
            headers={"User-Agent": "genlauncher-linux/0.1"},
        ) as client:
            for mod_name, url in self._requests:
                if not url:
                    self.image_missing.emit(mod_name)
                    continue

                safe_name = mod_name.replace("/", "_").replace("\\", "_")

                cached = self._cached(safe_name)
                if cached is not None:
                    try:
                        data = cached.read_bytes()
                    except OSError:
                        data = b""
                    if data:
                        self.image_ready.emit(mod_name, data)
                        continue
                    try:
                        cached.unlink()
                    except OSError:
                        pass

                marker = self._missing_marker(safe_name)
                if marker.exists():
                    # Already known-bad; do not hammer the host again.
                    self.image_missing.emit(mod_name)
                    continue

                data = self._fetch(client, url)

                # Rate-limit all requests (success or failure).
                elapsed = time.monotonic() - last_request
                if elapsed < self.MIN_INTERVAL:
                    time.sleep(self.MIN_INTERVAL - elapsed)
                last_request = time.monotonic()

                if data:
                    ext = self._sniff(data) or ".png"
                    try:
                        (self._cache_dir / f"{safe_name}{ext}").write_bytes(data)
                        marker.unlink(missing_ok=True)
                    except OSError:
                        pass
                    self.image_ready.emit(mod_name, data)
                    continue

                try:
                    marker.write_text(url, encoding="utf-8")
                except OSError:
                    pass
                self.image_missing.emit(mod_name)

        self.finished.emit()

    def _fetch(self, client, url: str) -> bytes:
        """GET with backoff. Returns image bytes, or b"" if unavailable."""
        for attempt in range(self.MAX_RETRIES):
            try:
                resp = client.get(url)
            except Exception as exc:
                log.debug("image fetch failed for %s: %s", url, exc)
                return b""

            if resp.status_code == 200:
                if self._sniff(resp.content):
                    return resp.content
                log.debug("non-image response from %s (%s)", url, resp.headers.get("content-type"))
                return b""

            if resp.status_code == 429 or resp.status_code >= 500:
                wait = self.BACKOFF * (attempt + 1)
                retry_after = resp.headers.get("retry-after")
                if retry_after and retry_after.isdigit() and int(retry_after) > 0:
                    wait = max(wait, float(retry_after))
                log.debug("HTTP %s from %s, retrying in %.0fs", resp.status_code, url, wait)
                time.sleep(wait)
                continue

            log.debug("HTTP %s for image %s", resp.status_code, url)
            return b""

        return b""
