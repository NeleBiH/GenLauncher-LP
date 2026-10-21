"""Dialog for supplying a download link when the catalog's link has died."""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from .theme import ui_font

log = logging.getLogger(__name__)

# Path endings that are a web page rather than a file.
_PAGE_SUFFIXES = (".html", ".htm", ".php", ".aspx", ".jsp")

# Share/preview pages from the hosts this catalog actually uses. These have no
# file extension, so a suffix check alone would wave them straight through.
_PAGE_HOSTS = {
    "onedrive.live.com",
    "1drv.ms",
}


def describe_link_problem(url: str) -> str:
    """Return why `url` cannot serve a file, or "" when it plausibly can."""
    from urllib.parse import parse_qs, urlparse

    if not url.strip():
        return "Enter a URL, or use Remove override."
    low = url.lower()
    if not low.startswith(("http://", "https://")):
        return "Must start with http:// or https://"

    parsed = urlparse(url)
    host = parsed.netloc.lower()
    segments = [s for s in parsed.path.split("/") if s]
    last = segments[-1].lower() if segments else ""
    query = parse_qs(parsed.query)

    if parsed.path.lower().rstrip("/").endswith(_PAGE_SUFFIXES):
        return "That is a web page, not a file. Paste the direct download link."

    if host in _PAGE_HOSTS:
        if last in ("embed", "preview", "view", "share", "download") or not last:
            return (
                "That is a OneDrive share/preview page. Open the share, choose "
                "Download, and copy the link that ends in a file."
            )
        if "?" not in url and "." not in last:
            return "That OneDrive link has no file name or query; it is a page link."

    if host in ("www.dropbox.com", "dropbox.com"):
        # /s/<id>, /sh/<id>, /scl/fo/<id> are share pages unless dl=1 is set.
        is_share_page = any(
            s.lower() in ("s", "sh", "scl") for s in segments[:-1]
        ) or segments[:1] == ["s"]
        dl = (query.get("dl") or [""])[0].lower()
        wants_file = dl in ("1", "true")
        if is_share_page and not wants_file:
            return (
                "That is a Dropbox share page. Add ?dl=1 to the end (or change "
                "dl=0 to dl=1) to download the file instead of the page."
            )
        if dl in ("0", "false"):
            return "That Dropbox link is set to preview mode (dl=0). Use dl=1."

    if host in ("drive.google.com", "docs.google.com"):
        if "export=download" not in low:
            return (
                "That is a Google Drive viewer link. Add &export=download to "
                "get the file."
            )
    return ""


class LinkOverrideDialog(QDialog):
    """Paste a working URL for a catalog entry, with an optional live check.

    Many bundled catalog entries still point at OneDrive shares that were
    revoked; the launcher cannot repair those, but the user can supply a
    replacement. The check deliberately does a ranged GET so a multi-gigabyte
    archive is not pulled just to be validated.
    """

    def __init__(self, mod_name: str, version: str, current: str = "", parent=None):
        super().__init__(parent)
        self._mod_name = mod_name
        self._version = version
        self.setWindowTitle("Set download link")
        self.setMinimumWidth(560)

        layout = QVBoxLayout(self)

        head = QLabel(f"<b>{mod_name}</b> — version {version}", self)
        head.setFont(ui_font(10))
        head.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(head)

        blurb = QLabel(
            "The link in the catalog no longer serves a file. Paste a direct "
            "download link here and it will be used from now on.",
            self,
        )
        blurb.setFont(ui_font(9))
        blurb.setWordWrap(True)
        layout.addWidget(blurb)

        layout.addWidget(QLabel("Download URL:", self))
        self.url_edit = QLineEdit(current, self)
        self.url_edit.setFont(ui_font(9))
        self.url_edit.setPlaceholderText("https://...")
        self.url_edit.selectAll()
        layout.addWidget(self.url_edit)

        status = QLabel("", self)
        status.setFont(ui_font(9))
        status.setWordWrap(True)
        self._status = status
        layout.addWidget(status)

        row = QHBoxLayout()
        self.check_btn = QPushButton("Test link", self)
        self.check_btn.setFont(ui_font(9))
        self.check_btn.clicked.connect(self._on_test)
        row.addWidget(self.check_btn)

        row.addStretch(1)

        remove_btn = QPushButton("Remove override", self)
        remove_btn.setFont(ui_font(9))
        remove_btn.clicked.connect(self._on_remove)
        row.addWidget(remove_btn)

        cancel_btn = QPushButton("Cancel", self)
        cancel_btn.setFont(ui_font(9))
        cancel_btn.clicked.connect(self.reject)
        row.addWidget(cancel_btn)

        save_btn = QPushButton("Save", self)
        save_btn.setFont(ui_font(10))
        save_btn.setDefault(True)
        save_btn.clicked.connect(self._on_save)
        row.addWidget(save_btn)
        layout.addLayout(row)

    @property
    def url(self) -> str:
        return self.url_edit.text().strip()

    @property
    def removed(self) -> bool:
        return self.property("removed") is True

    def _on_remove(self) -> None:
        self.setProperty("removed", True)
        self.accept()

    def _on_save(self) -> None:
        url = self.url
        problem = describe_link_problem(url)
        if problem:
            self._status.setText(problem)
            return
        self.accept()

    def _on_test(self) -> None:
        """Probe the URL with a ranged GET and report what actually comes back."""
        url = self.url
        problem = describe_link_problem(url)
        if problem:
            self._status.setText(problem)
            return

        self.check_btn.setEnabled(False)
        self._status.setText("Checking...")

        from PySide6.QtWidgets import QApplication

        app = QApplication.instance()
        if app is not None:
            app.processEvents()

        import httpx

        try:
            with httpx.Client(
                follow_redirects=True, timeout=25.0
            ) as client:
                resp = client.get(url, headers={"Range": "bytes=0-1023"})
            ctype = resp.headers.get("content-type", "").split(";")[0].strip()
            total = resp.headers.get("content-range", "")
            size = ""
            if "/" in total:
                try:
                    size = f", full size {int(total.split('/')[-1]) / 1e6:.1f} MB"
                except ValueError:
                    size = ""

            if ctype == "text/html":
                self._status.setText(
                    f"Server returned an HTML page ({len(resp.content)} B), not a "
                    "file. The share is probably dead or needs a browser session."
                )
            elif resp.status_code >= 400:
                self._status.setText(f"Server said HTTP {resp.status_code}.")
            else:
                self._status.setText(
                    f"OK — {ctype or 'unknown type'}, "
                    f"{len(resp.content)} B sampled{size}."
                )
        except Exception as exc:
            self._status.setText(f"{type(exc).__name__}: {exc}")
        finally:
            self.check_btn.setEnabled(True)
