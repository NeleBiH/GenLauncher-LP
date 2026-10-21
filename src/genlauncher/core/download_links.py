"""Turn catalog download links into links that actually return a file.

Many catalog entries point at an interstitial page rather than the file:

* Dropbox share links need ``?dl=1`` or you get the HTML preview page.
* OneDrive ``/embed?`` and bare ``/?cid=...&id=...`` links must be rewritten to the
  ``/download?`` form.

Based on Rewritten's download-link resolver; the transformations themselves are
just the hosts' own URL conventions. This version is stricter:

* it also fixes Dropbox links that carry no ``dl=`` parameter at all
  (the reference only rewrites an existing ``?dl=0``),
* it parses the query with ``urlsplit``/``parse_qs`` instead of splitting the
  string, and
* it returns the link untouched when ``cid``/``id`` are missing rather than
  formatting an empty ``cid=&resid=&authkey=`` URL.

Without this, "install" happily saves an HTML file and then fails to unpack it.
"""

from __future__ import annotations

import logging
from urllib.parse import parse_qs, urlsplit

log = logging.getLogger(__name__)

DROPBOX_HOSTS = ("www.dropbox.com", "dropbox.com")
ONEDRIVE_HOSTS = ("onedrive.live.com", "www.onedrive.live.com")


def parse_download_link(link: str | None) -> str:
    """Best-effort conversion of a catalog link into a direct download URL."""
    if not link:
        return ""

    link = link.strip()
    if "www.dropbox.com" in link or "dropbox.com/" in link:
        link = _dropbox(link)
    if "onedrive.live.com" in link:
        link = _onedrive(link)
    return link


def _dropbox(link: str) -> str:
    """dl=0 shows a preview page; dl=1 starts the download."""
    if "dl=0" in link:
        return link.replace("dl=0", "dl=1")
    if "dl=" not in link:
        return link + ("&" if "?" in link else "?") + "dl=1"
    return link


def _onedrive(link: str) -> str:
    if "/embed" in link:
        # The simplest case, and by far the most common in the catalog.
        return link.replace("/embed", "/download", 1)

    # https://onedrive.live.com/?authkey=..&cid=..&id=..&parId=..&o=OneUp
    parts = urlsplit(link)
    query = parse_qs(parts.query)

    cid = (query.get("cid") or [None])[0]
    authkey = (query.get("authkey") or [None])[0]
    resid = (query.get("id") or query.get("resid") or [None])[0]

    if not cid or not resid:
        log.debug("cannot rewrite OneDrive link, missing cid/id: %s", link)
        return link

    return (
        "https://onedrive.live.com/download"
        f"?cid={cid}&resid={resid}&authkey={authkey or ''}"
    )


def looks_like_archive(url: str) -> bool:
    """True if the URL itself ends in something we know how to unpack.

    Only a hint: some hosts hide the real name behind a redirect, so the caller
    should still check the downloaded file rather than trusting this.
    """
    path = urlsplit(url).path.casefold()
    return path.endswith((".zip", ".7z", ".rar"))