"""Download and install TheSuperHackers community-patched game executables.

Uses GitHub Releases API to fetch pre-built weekly builds. No Docker or
build toolchain required — the exe is downloaded as a zip and extracted
into the game directory alongside the retail executable.
"""

from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import httpx

log = logging.getLogger(__name__)

GITHUB_REPO = "TheSuperHackers/GeneralsGameCode"
GITHUB_API_LATEST = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"

ASSET_PREFIX: dict[str, str] = {
    "zh": "generalszh-weekly-",
    "generals": "generals-weekly-",
}


class PatchError(RuntimeError):
    """Any failure during the patch workflow."""


@dataclass
class ReleaseInfo:
    """Metadata for a single GitHub release."""

    tag: str  # "weekly-2026-10-03"
    asset_name: str  # "generalszh-weekly-2026-10-03.zip"
    download_url: str
    size: int
    published_at: str


def fetch_latest_release(game: str = "zh") -> ReleaseInfo:
    """Query GitHub for the newest weekly release."""
    prefix = ASSET_PREFIX.get(game)
    if not prefix:
        raise PatchError(f"Unknown game: {game}")

    try:
        resp = httpx.get(
            GITHUB_API_LATEST,
            headers={"Accept": "application/vnd.github+json"},
            timeout=15,
            follow_redirects=True,
        )
    except httpx.HTTPError as exc:
        raise PatchError(f"Cannot reach GitHub: {exc}") from exc

    if resp.status_code == 403:
        reset = resp.headers.get("X-RateLimit-Reset", "")
        raise PatchError(
            f"GitHub API rate limit reached. Try again later."
            + (f" (resets at epoch {reset})" if reset else "")
        )
    if resp.status_code != 200:
        raise PatchError(f"GitHub API returned {resp.status_code}")

    data = resp.json()
    tag = data.get("tag_name", "")

    for asset in data.get("assets", []):
        name = asset.get("name", "")
        if name.startswith(prefix) and name.endswith(".zip"):
            return ReleaseInfo(
                tag=tag,
                asset_name=name,
                download_url=asset["browser_download_url"],
                size=asset.get("size", 0),
                published_at=data.get("published_at", ""),
            )

    raise PatchError(f"No {game.upper()} build found in release {tag}")


def needs_update(installed_tag: str, latest: ReleaseInfo) -> bool:
    """True when the installed version differs from the latest release."""
    return installed_tag != latest.tag


def download_release(
    release: ReleaseInfo,
    cache_dir: Path,
    progress: Callable | None = None,
    cancel: Callable[[], bool] | None = None,
) -> Path:
    """Download the release zip into cache_dir. Returns path to the archive."""
    from .downloader import Downloader

    with Downloader() as dl:
        return dl.download_to_cache(
            release.download_url,
            cache_dir,
            filename=release.asset_name,
            progress=progress,
            cancel=cancel,
            expect_archive=True,
        )


def install_patch(archive: Path, game_dir: Path, game: str = "zh") -> list[Path]:
    """Extract exe/dll files from the release archive into game_dir.

    Returns the list of files placed. The original game files are never
    overwritten — the community exe has a distinct name (generalszh.exe
    vs generals.exe).
    """
    from .installer import extract_archive

    if not os.access(game_dir, os.W_OK):
        raise PatchError(f"Cannot write to game directory: {game_dir}")

    # Extract to a temporary directory next to the game folder.
    tmp = game_dir / "_community_patch_tmp"
    if tmp.exists():
        shutil.rmtree(tmp, ignore_errors=True)

    try:
        extract_archive(archive, tmp)

        # Collect .exe and .dll files from the extracted tree.
        placed: list[Path] = []
        for root, _dirs, files in os.walk(tmp):
            for fname in files:
                lower = fname.lower()
                if lower.endswith((".exe", ".dll")):
                    src = Path(root) / fname
                    dest = game_dir / fname
                    shutil.copy2(str(src), str(dest))
                    placed.append(dest)
                    log.info("installed: %s", dest)

        if not placed:
            raise PatchError("No executable files found in the release archive")

        return placed

    finally:
        if tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)
