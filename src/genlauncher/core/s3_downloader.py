"""S3/MinIO multi-file download for mod packages.

Most catalog entries (76 out of 96) store individual files on a MinIO S3
server at gen.insave.ovh:9000.  The listing API works on HTTP port 9000
(not HTTPS 443, which returns 403 via nginx).  Credentials are public
read-only keys shipped by every GenLauncher client.

Download flow (mirrors GenLauncherGO-Rewritten S3PackageUpdater):

1. List objects in bucket/folder via MinIO SDK -> file manifest with ETag+size
2. Copy unchanged files from a previously installed version (hash match)
3. Download remaining files with resume, concurrent transfers, hash verification
4. Convert .big -> .gib after download
5. Stage into final install folder atomically
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import httpx

log = logging.getLogger(__name__)

# Public read-only credentials from Rewritten's S3 catalog defaults.
# These are NOT secrets — they ship in every GenLauncher client and grant read-only access.
DEFAULT_ACCESS_KEY = "S58TYR9ISEZV8PBP8QG1"
DEFAULT_SECRET_KEY = "b2RU1oqVU5toJRnb4gODrXX8sBSgoLcHRX6qPWxj"

# File extensions that require hash verification (from GenLauncherGO-Rewritten).
HASH_CHECK_EXTENSIONS = frozenset({
    ".w3d", ".big", ".bik", ".gib", ".dds",
    ".tga", ".ini", ".scb", ".wnd", ".csf", ".str",
})

MAX_CONCURRENT_DOWNLOADS = 6
MAX_HASH_ATTEMPTS = 3
MAX_DOWNLOAD_ATTEMPTS = 5
IDLE_TIMEOUT = 30.0
CHUNK = 1024 * 1024  # 1 MB buffer


class S3DownloadError(RuntimeError):
    pass


class S3DownloadCancelled(S3DownloadError):
    pass


@dataclass
class RemoteFile:
    """One file in the S3 manifest (from ListObjects)."""
    name: str
    etag: str  # MD5 hash (without quotes)
    size: int


@dataclass
class S3Progress:
    """Progress snapshot for the entire S3 package download."""
    downloaded: int
    total: int
    current_file: str = ""
    files_done: int = 0
    files_total: int = 0
    elapsed: float = 0.0

    @property
    def fraction(self) -> float:
        return (self.downloaded / self.total) if self.total else 0.0

    @property
    def speed_str(self) -> str:
        if self.elapsed <= 0:
            return ""
        bps = self.downloaded / self.elapsed
        if bps >= 1048576:
            return f"{bps / 1048576:.1f} MB/s"
        return f"{bps / 1024:.0f} KB/s"


S3ProgressCallback = Callable[[S3Progress], None]


def _monotonic_progress(callback: S3ProgressCallback) -> S3ProgressCallback:
    """Wrap a progress callback so reported values never regress.

    With concurrent downloads, completion events can arrive out of order.
    This normalizer ensures the UI progress bar never jumps backwards
    (Rewritten MonotonicPackageProgress pattern).
    """
    import threading
    lock = threading.Lock()
    peak = {"downloaded": 0, "files_done": 0}

    def _report(p: S3Progress) -> None:
        with lock:
            peak["downloaded"] = max(peak["downloaded"], p.downloaded)
            peak["files_done"] = max(peak["files_done"], p.files_done)
            normalized = S3Progress(
                downloaded=peak["downloaded"],
                total=max(p.total, peak["downloaded"]),
                current_file=p.current_file,
                files_done=peak["files_done"],
                files_total=p.files_total,
                elapsed=p.elapsed,
            )
        callback(normalized)

    return _report


def _normalize_etag(etag: str) -> str:
    """Strip quotes and whitespace from S3 ETags."""
    return etag.strip().strip('"').lower()


def _should_check_hash(filename: str) -> bool:
    """Check if this file extension requires hash verification."""
    return Path(filename).suffix.lower() in HASH_CHECK_EXTENSIONS


def _md5_of(path: Path) -> str:
    """Compute MD5 hex digest of a file."""
    digest = hashlib.md5()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(CHUNK)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest().lower()


def _gib_path(path: Path) -> Path:
    """Return the .gib variant of a .big file path."""
    return path.with_suffix(".gib")


def _convert_big_to_gib(path: Path) -> Path:
    """Rename .big -> .gib after download (game modding convention)."""
    if path.suffix.lower() != ".big":
        return path
    gib = _gib_path(path)
    if gib.exists():
        gib.unlink()
    path.rename(gib)
    return gib


def _installed_file_path(filename: str, dest_dir: Path) -> Path:
    """Resolve where a manifest file should be placed, handling .big->.gib."""
    p = dest_dir / filename.replace("\\", "/")
    if p.suffix.lower() == ".big":
        return _gib_path(p)
    return p


def list_s3_objects(
    endpoint: str,
    bucket: str,
    prefix: str,
    access_key: str = "",
    secret_key: str = "",
) -> list[RemoteFile]:
    """List all objects in an S3 bucket/prefix. Returns manifest entries.

    Uses HTTP (not HTTPS) on port 9000 — the MinIO API port.
    HTTPS via nginx on port 443 blocks listing with 403.
    """
    from minio import Minio

    ak = access_key or DEFAULT_ACCESS_KEY
    sk = secret_key or DEFAULT_SECRET_KEY

    # MinIO listing works on HTTP, not HTTPS (nginx blocks listing on 443).
    client = Minio(endpoint, secure=False, access_key=ak, secret_key=sk)

    normalized_prefix = prefix.rstrip("/") + "/"
    files: list[RemoteFile] = []
    for obj in client.list_objects(bucket, prefix=normalized_prefix, recursive=True):
        # Strip prefix from object key to get relative filename.
        name = obj.object_name
        if name.startswith(normalized_prefix):
            name = name[len(normalized_prefix):]
        if not name:
            continue
        files.append(RemoteFile(
            name=name,
            etag=_normalize_etag(obj.etag or ""),
            size=obj.size,
        ))

    log.info("S3 manifest: %d files, %.0f MB total from %s/%s/%s",
             len(files), sum(f.size for f in files) / 1048576,
             endpoint, bucket, prefix)
    return files


def _copy_reusable_files(
    manifest: list[RemoteFile],
    prev_dir: Path,
    dest_dir: Path,
) -> set[str]:
    """Copy files from a previous version that match the manifest hash.

    Returns set of filenames that were reused (no download needed).
    """
    reused: set[str] = set()
    if not prev_dir or not prev_dir.is_dir():
        return reused

    for remote in manifest:
        if not _should_check_hash(remote.name):
            continue
        installed = _installed_file_path(remote.name, prev_dir)
        if not installed.is_file():
            continue
        try:
            local_hash = _md5_of(installed)
            if local_hash == remote.etag:
                target = _installed_file_path(remote.name, dest_dir)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(installed, target)
                reused.add(remote.name)
                log.debug("Reused %s from previous version", remote.name)
        except OSError:
            continue
    return reused


def _download_one_file(
    url: str,
    dest: Path,
    expected_size: int,
    timeout: float = IDLE_TIMEOUT,
) -> int:
    """Download a single file with HTTP Range resume. Returns bytes downloaded.

    Raises on failure after MAX_DOWNLOAD_ATTEMPTS retries.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    existing = dest.stat().st_size if dest.is_file() else 0

    if expected_size > 0:
        if existing == expected_size:
            return existing  # Already complete
        if existing > expected_size:
            # Larger than expected — discard and redownload.
            dest.unlink(missing_ok=True)
            existing = 0

    headers = {"Range": f"bytes={existing}-"} if existing else {}
    last_error: Exception | None = None

    for attempt in range(1, MAX_DOWNLOAD_ATTEMPTS + 1):
        try:
            with httpx.Client(timeout=timeout, follow_redirects=True) as client:
                with client.stream("GET", url, headers=headers) as resp:
                    if existing and resp.status_code == 200:
                        # Server doesn't support range, restart.
                        existing = 0
                        dest.unlink(missing_ok=True)
                    elif resp.status_code not in (200, 206):
                        resp.raise_for_status()

                    mode = "ab" if existing else "wb"
                    done = existing
                    last_data = time.monotonic()

                    with dest.open(mode) as fh:
                        for chunk in resp.iter_bytes(CHUNK):
                            if not chunk:
                                if time.monotonic() - last_data > timeout:
                                    raise TimeoutError("idle timeout")
                                continue
                            fh.write(chunk)
                            done += len(chunk)
                            last_data = time.monotonic()
                return done
        except (httpx.HTTPError, TimeoutError, OSError) as exc:
            last_error = exc
            log.warning("Download attempt %d/%d failed for %s: %s",
                        attempt, MAX_DOWNLOAD_ATTEMPTS, dest.name, exc)
            if attempt >= MAX_DOWNLOAD_ATTEMPTS:
                break
            # Resume from current position on next attempt.
            existing = dest.stat().st_size if dest.is_file() else 0
            headers = {"Range": f"bytes={existing}-"} if existing else {}
            time.sleep(min(2 ** (attempt - 1), 30))  # Exponential backoff

    raise S3DownloadError(
        f"Download failed after {MAX_DOWNLOAD_ATTEMPTS} attempts: {dest.name}"
    ) from last_error


def _build_download_url(endpoint: str, bucket: str, prefix: str, filename: str) -> str:
    """Build a direct HTTPS URL for an S3 object.

    Uses HTTPS (port 443 via nginx) for file downloads since individual
    file downloads work there, unlike listing.
    """
    host = endpoint.split(":")[0]  # Strip port if present
    normalized_prefix = prefix.rstrip("/")
    # URL-encode the filename parts but not slashes.
    from urllib.parse import quote
    encoded_name = quote(filename, safe="/")
    return f"https://{host}/{bucket}/{normalized_prefix}/{encoded_name}"


class S3Downloader:
    """Downloads a mod package from S3/MinIO with multi-file parallel transfer."""

    def __init__(self, max_workers: int = MAX_CONCURRENT_DOWNLOADS) -> None:
        self._max_workers = max_workers

    def download_package(
        self,
        endpoint: str,
        bucket: str,
        prefix: str,
        dest_dir: Path,
        access_key: str = "",
        secret_key: str = "",
        prev_version_dir: Path | None = None,
        progress: S3ProgressCallback | None = None,
        cancel: Callable[[], bool] | None = None,
    ) -> Path:
        """Download all files for a mod package from S3.

        Args:
            endpoint: MinIO endpoint (e.g. "gen.insave.ovh:9000")
            bucket: S3 bucket name
            prefix: S3 folder/prefix
            dest_dir: Target directory for downloaded files
            access_key: S3 access key (uses default if empty)
            secret_key: S3 secret key (uses default if empty)
            prev_version_dir: Previous installed version for file reuse
            progress: Callback for download progress
            cancel: Callable returning True to cancel

        Returns: dest_dir
        """
        raw_cb = progress or (lambda p: None)
        cb = _monotonic_progress(raw_cb)
        cancelled = cancel or (lambda: False)

        # Step 1: List objects.
        log.info("Listing S3 objects: %s/%s/%s", endpoint, bucket, prefix)
        manifest = list_s3_objects(endpoint, bucket, prefix, access_key, secret_key)
        if not manifest:
            raise S3DownloadError(f"Empty S3 manifest for {bucket}/{prefix}")

        total_size = sum(f.size for f in manifest)
        dest_dir = Path(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        t0 = time.monotonic()

        # Step 2: Copy unchanged files from previous version.
        reused = _copy_reusable_files(manifest, prev_version_dir, dest_dir) if prev_version_dir else set()
        reused_bytes = sum(f.size for f in manifest if f.name in reused)

        # Step 3: Determine what needs downloading.
        to_download = [f for f in manifest if f.name not in reused]
        log.info("S3 download: %d files to fetch, %d reused, %.0f MB remaining",
                 len(to_download), len(reused), (total_size - reused_bytes) / 1048576)

        if cancelled():
            raise S3DownloadCancelled("cancelled before download started")

        # Step 4: Download files concurrently.
        downloaded_bytes = reused_bytes
        files_done = len(reused)

        def _report():
            cb(S3Progress(
                downloaded=downloaded_bytes,
                total=total_size,
                current_file="",
                files_done=files_done,
                files_total=len(manifest),
                elapsed=time.monotonic() - t0,
            ))

        _report()

        if not to_download:
            # All files reused from previous version.
            cb(S3Progress(
                downloaded=total_size, total=total_size,
                files_done=len(manifest), files_total=len(manifest),
                elapsed=time.monotonic() - t0,
            ))
            return dest_dir

        errors: list[tuple[str, Exception]] = []

        with ThreadPoolExecutor(max_workers=self._max_workers) as pool:
            futures = {}
            for remote in to_download:
                if cancelled():
                    raise S3DownloadCancelled("cancelled")
                url = _build_download_url(endpoint, bucket, prefix, remote.name)
                file_dest = dest_dir / remote.name.replace("\\", "/")
                fut = pool.submit(
                    self._download_and_verify,
                    url, file_dest, remote,
                )
                futures[fut] = remote

            for fut in as_completed(futures):
                remote = futures[fut]
                try:
                    fut.result()
                    downloaded_bytes += remote.size
                    files_done += 1
                    _report()
                except S3DownloadCancelled:
                    raise
                except Exception as exc:
                    errors.append((remote.name, exc))
                    log.error("Failed to download %s: %s", remote.name, exc)

                if cancelled():
                    # Cancel remaining futures.
                    for f in futures:
                        f.cancel()
                    raise S3DownloadCancelled("cancelled during download")

        if errors:
            names = ", ".join(n for n, _ in errors[:5])
            raise S3DownloadError(
                f"Failed to download {len(errors)} file(s): {names}"
            )

        # Final progress report.
        cb(S3Progress(
            downloaded=total_size, total=total_size,
            files_done=len(manifest), files_total=len(manifest),
            elapsed=time.monotonic() - t0,
        ))
        return dest_dir

    def _download_and_verify(
        self,
        url: str,
        dest: Path,
        remote: RemoteFile,
    ) -> None:
        """Download one file with hash verification and retry."""
        for attempt in range(1, MAX_HASH_ATTEMPTS + 1):
            _download_one_file(url, dest, remote.size)

            # Convert .big -> .gib after download.
            actual_path = _convert_big_to_gib(dest)

            # Hash verification.
            if _should_check_hash(remote.name) and remote.etag:
                actual_hash = _md5_of(actual_path)
                if actual_hash != remote.etag:
                    log.warning(
                        "Hash mismatch for %s (attempt %d/%d): expected %s, got %s",
                        remote.name, attempt, MAX_HASH_ATTEMPTS,
                        remote.etag, actual_hash,
                    )
                    actual_path.unlink(missing_ok=True)
                    if attempt >= MAX_HASH_ATTEMPTS:
                        raise S3DownloadError(
                            f"Hash verification failed after {MAX_HASH_ATTEMPTS} "
                            f"attempts for {remote.name}"
                        )
                    time.sleep(2)
                    continue
            break  # Success
