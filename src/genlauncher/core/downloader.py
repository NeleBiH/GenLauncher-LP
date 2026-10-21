"""HTTP downloads: streaming, progress, resume, cancellation.

Downloads land on a `.part` file and are renamed atomically on success, so an
interrupted download can never be mistaken for a complete one. Resume uses HTTP
Range, which the mod host does support (verified: 206 responses).
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import httpx

log = logging.getLogger(__name__)

USER_AGENT = "GenLauncherLinux/0.1 (+https://github.com/p0ls3r/GenLauncher)"
CHUNK = 1024 * 1024  # 1 MB buffer (matches Rewritten's DefaultBufferSize)
MAX_RETRIES = 5
IDLE_TIMEOUT = 30.0  # seconds without progress before retry (Rewritten: 30s)
MAX_RETRY_DELAY = 30.0  # cap for exponential backoff
INITIAL_RETRY_DELAY = 1.0  # first retry delay


class DownloadError(RuntimeError):
    pass


class DownloadCancelled(DownloadError):
    pass


class _IdleTimeout(Exception):
    """Internal: download stalled with no data for IDLE_TIMEOUT seconds."""
    pass


@dataclass
class Progress:
    """Snapshot handed to the progress callback."""

    downloaded: int
    total: int  # 0 when the server did not tell us
    elapsed: float = 0.0  # seconds since download started

    @property
    def fraction(self) -> float:
        return (self.downloaded / self.total) if self.total else 0.0

    @property
    def megabytes(self) -> str:
        return f"{self.downloaded / 1048576:.1f}"

    @property
    def total_megabytes(self) -> str:
        return f"{self.total / 1048576:.1f}" if self.total else "?"

    @property
    def speed_str(self) -> str:
        """Human-readable download speed (KB/s or MB/s)."""
        if self.elapsed <= 0:
            return ""
        bps = self.downloaded / self.elapsed
        if bps >= 1048576:
            return f"{bps / 1048576:.1f} MB/s"
        return f"{bps / 1024:.0f} KB/s"


ProgressCallback = Callable[[Progress], None]


def _retry_sleep(attempt: int) -> None:
    """Exponential backoff capped at MAX_RETRY_DELAY (Rewritten pattern)."""
    delay = min(INITIAL_RETRY_DELAY * (2 ** max(0, attempt - 1)), MAX_RETRY_DELAY)
    time.sleep(delay)


def _callback(cb: ProgressCallback | None) -> ProgressCallback:
    return cb or (lambda p: None)


class Downloader:
    """Reusable downloader. One instance can be shared across threads."""

    def __init__(
        self,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._timeout = timeout
        self._transport = transport
        self._client: httpx.Client | None = None

    def __enter__(self) -> "Downloader":
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

    # ---- public API -----------------------------------------------------

    def download(
        self,
        url: str,
        dest: Path,
        progress: ProgressCallback | None = None,
        cancel: Callable[[], bool] | None = None,
        resume: bool = True,
        expected_hash: str | None = None,
        expect_archive: bool = True,
    ) -> Path:
        """Fetch `url` to `dest`. Returns dest.

        `cancel` is polled between chunks; raise DownloadCancelled from the caller
        side by returning True. `expected_hash` is an MD5 hex digest; when given and
        mismatched, the partial file is discarded.

        With `expect_archive` the response Content-Type is checked first. A large
        part of the catalog points at a OneDrive or ModDB landing page rather than
        the file, and without this we would happily save an HTML page into the cache
        and only discover the problem when unpacking fails.
        """
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_suffix(dest.suffix + ".part")
        cb = _callback(progress)
        cancelled = cancel or (lambda: False)

        if not resume and part.exists():
            part.unlink()

        last_error: Exception | None = None
        for attempt in range(1, MAX_RETRIES + 1):
            existing = part.stat().st_size if resume and part.is_file() else 0
            headers: dict[str, str] = {}
            if existing:
                headers["Range"] = f"bytes={existing}-"
            try:
                with self._http.stream("GET", url, headers=headers) as response:
                    if expect_archive and not existing:
                        problem = _archive_content_problem(url, response)
                        if problem:
                            raise DownloadError(problem)

                    # Validate resume acceptance (Rewritten pattern).
                    if existing and response.status_code == 200:
                        # Server didn't honor range; restart from zero.
                        log.info("Server did not honor range for %s; restarting", url)
                        existing = 0
                        part.unlink(missing_ok=True)
                    elif existing and response.status_code == 206:
                        # Verify server starts at expected byte.
                        content_range = response.headers.get("Content-Range", "")
                        if content_range and f"bytes {existing}-" not in content_range:
                            log.warning("Unexpected range response for %s; restarting", url)
                            existing = 0
                            part.unlink(missing_ok=True)
                    elif existing and response.status_code not in (200, 206):
                        response.raise_for_status()

                    total = existing + int(response.headers.get("Content-Length", 0) or 0)
                    mode = "ab" if existing else "wb"

                    done = existing
                    t0 = time.monotonic()
                    last_chunk_time = time.monotonic()
                    cb(Progress(done, total, 0.0))

                    with part.open(mode) as fh:
                        for chunk in response.iter_bytes(CHUNK):
                            if cancelled():
                                raise DownloadCancelled(f"cancelled: {url}")
                            if not chunk:
                                # Check idle timeout
                                if time.monotonic() - last_chunk_time > IDLE_TIMEOUT:
                                    raise _IdleTimeout(url)
                                continue
                            fh.write(chunk)
                            done += len(chunk)
                            last_chunk_time = time.monotonic()
                            cb(Progress(done, total, time.monotonic() - t0))
                break  # success

            except _IdleTimeout:
                last_error = DownloadError(
                    f"connection stalled for {url} (attempt {attempt}/{MAX_RETRIES})"
                )
                log.warning("download stalled, retrying (%d/%d): %s", attempt, MAX_RETRIES, url)
                if attempt >= MAX_RETRIES:
                    raise last_error
                _retry_sleep(attempt)
                continue

            except DownloadCancelled:
                raise
            except DownloadError:
                raise
            except httpx.HTTPStatusError as exc:
                raise DownloadError(f"HTTP {exc.response.status_code} for {url}") from exc
            except httpx.HTTPError as exc:
                last_error = DownloadError(f"network error for {url}: {exc}")
                log.warning("network error, retrying (%d/%d): %s", attempt, MAX_RETRIES, exc)
                if attempt >= MAX_RETRIES:
                    raise last_error
                _retry_sleep(attempt)
                continue

        if total and done < total:
            raise DownloadError(f"incomplete download for {url}: {done}/{total} bytes")

        if expected_hash:
            actual = md5_of(part)
            if actual.lower() != expected_hash.lower():
                part.unlink(missing_ok=True)
                raise DownloadError(
                    f"hash mismatch for {url}: expected {expected_hash}, got {actual}"
                )

        part.replace(dest)
        return dest

    def download_to_cache(
        self,
        url: str,
        cache_dir: Path,
        filename: str | None = None,
        **kwargs,
    ) -> Path:
        """Download into a cache dir, reusing an already complete file."""
        import hashlib

        cache_dir = Path(cache_dir)
        cache_dir.mkdir(parents=True, exist_ok=True)
        name = filename or _filename_from_url(url)
        digest = hashlib.sha256(url.encode()).hexdigest()[:10]
        stem, dot, ext = name.rpartition(".")
        target = cache_dir / (f"{stem}.{digest}.{ext}" if dot else f"{name}.{digest}")

        if target.is_file() and target.stat().st_size > 0:
            kwargs.setdefault("resume", False)
            # Still report a full progress tick so callers do not appear stuck.
            _callback(kwargs.get("progress"))(Progress(target.stat().st_size, target.stat().st_size))
            return target

        return self.download(url, target, **kwargs)


# Content types that mean "this is the file", or at least "this is binary data".
ARCHIVE_CONTENT_TYPES = (
    "application/zip",
    "application/x-zip-compressed",
    "application/x-7z-compressed",
    "application/x-rar",
    "application/x-rar-compressed",
    "application/vnd.rar",
    "application/octet-stream",
    "application/binary",
    "application/x-gzip",
)

# Magic bytes, checked once the body has started arriving.
_ARCHIVE_MAGIC = (
    b"PK\x03\x04",  # zip
    b"PK\x05\x06",  # empty zip
    b"7z\xbc\xaf\x27\x1c",  # 7z
    b"Rar!\x1a\x07",  # rar
    b"\x1f\x8b",  # gzip
)


def _archive_content_problem(url: str, response: httpx.Response) -> str | None:
    """Return a human-readable problem if this response is not a file download."""
    content_type = (response.headers.get("Content-Type") or "").split(";")[0].strip().lower()
    disposition = (response.headers.get("Content-Disposition") or "").lower()

    if "attachment" in disposition:
        return None
    if any(content_type.startswith(t) for t in ARCHIVE_CONTENT_TYPES):
        return None
    if not content_type:
        return None  # unknown: let the download proceed

    if content_type.startswith("text/html"):
        return (
            f"{url}\n  -> returned an HTML page, not a file ({content_type}).\n"
            "     This catalog link points at a share/preview page that no longer\n"
            "     serves the archive directly. Use the mod's news or support page."
        )
    return (
        f"{url}\n  -> unexpected content type {content_type!r}; refusing to save it "
        "as a mod archive."
    )


def sniff_archive(path: Path, head: int = 8) -> str | None:
    """Return the archive type of an existing file by magic bytes, or None."""
    try:
        with open(path, "rb") as fh:
            magic = fh.read(head)
    except OSError:
        return None
    for candidate in _ARCHIVE_MAGIC:
        if magic.startswith(candidate):
            return {
                b"PK": "zip",
                b"7z": "7z",
                b"Rar": "rar",
                b"\x1f\x8b": "gz",
            }.get(candidate[:2], "archive")
    return None


def _filename_from_url(url: str) -> str:
    from urllib.parse import unquote, urlparse

    tail = unquote(urlparse(url).path.rsplit("/", 1)[-1])
    return tail or "download.bin"


def md5_of(path: Path, chunk: int = CHUNK) -> str:
    digest = hashlib.md5()
    with Path(path).open("rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def free_space(path: Path) -> int:
    path = Path(path)
    probe = path if path.exists() else path.parent
    try:
        return shutil.disk_usage(probe).free
    except OSError:
        return 0


def guess_content_length(url: str, client: httpx.Client | None = None) -> int:
    """HEAD a URL for its size; 0 when unknown. Used to warn before downloading."""
    owns = client is None
    http = client or httpx.Client(timeout=15.0, follow_redirects=True,
                                  headers={"User-Agent": USER_AGENT})
    try:
        return int(http.head(url).headers.get("Content-Length", 0) or 0)
    except (httpx.HTTPError, ValueError, OSError):
        return 0
    finally:
        if owns:
            http.close()