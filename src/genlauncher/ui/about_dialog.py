"""About dialog — credits, links, and feature overview."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QDialog,
    QLabel,
    QPushButton,
    QScrollArea,
    QWidget,
)

from .theme import ui_font, DEFAULT_COLORS


class AboutDialog(QDialog):
    """About / credits dialog."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("About GenLauncher-LP")
        self.setFixedSize(620, 520)
        self.setWindowFlags(
            Qt.WindowType.Dialog | Qt.WindowType.WindowCloseButtonHint
        )
        self._build_ui()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setBrush(QColor(DEFAULT_COLORS.dark_bg))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRect(self.rect())
        painter.setPen(QPen(QColor(DEFAULT_COLORS.dark_fill), 2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(1, 1, self.width() - 2, self.height() - 2)
        painter.end()

    def _build_ui(self):
        c = DEFAULT_COLORS

        from importlib.metadata import version as pkg_version
        try:
            ver = pkg_version("genlauncher-linux")
        except Exception:
            ver = "0.1.1"

        # Title
        title = QLabel(f"GenLauncher-LP v{ver}", self)
        title.setGeometry(20, 15, 580, 32)
        title.setFont(ui_font(16, bold=True))
        title.setStyleSheet(f"color: {c.active};")

        subtitle = QLabel("Linux Port  —  Mod Manager for C&C Generals / Zero Hour", self)
        subtitle.setGeometry(20, 48, 580, 22)
        subtitle.setFont(ui_font(11))
        subtitle.setStyleSheet(f"color: {c.text};")

        # Scrollable content
        scroll = QScrollArea(self)
        scroll.setGeometry(15, 80, 590, 390)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet(
            f"QScrollArea {{ border: 1px solid {c.dark_fill}; background: transparent; }}"
        )

        content = QWidget()
        body = QLabel(content)
        body.setWordWrap(True)
        body.setOpenExternalLinks(True)
        body.setTextFormat(Qt.TextFormat.RichText)
        body.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        body.setFont(ui_font(10))
        body.setStyleSheet(f"color: {c.text};")
        body.setContentsMargins(10, 8, 10, 8)
        body.setFixedWidth(555)
        body.setText(self._about_html())
        body.adjustSize()

        content.setMinimumSize(555, body.height() + 16)
        scroll.setWidget(content)

        # Close button
        close_btn = QPushButton("Close", self)
        close_btn.setGeometry(250, 478, 120, 32)
        close_btn.setFont(ui_font(11))
        close_btn.clicked.connect(self.accept)

    @staticmethod
    def _about_html() -> str:
        link = (
            lambda url, text: f'<a href="{url}" style="color: #E8C252;">{text}</a>'
        )

        rewritten = link(
            "https://github.com/GenLauncherRewritten/GenLauncherRewritten",
            "GenLauncher Rewritten",
        )
        superhackers = link(
            "https://github.com/TheSuperHackers",
            "TheSuperHackers",
        )
        superhackers_code = link(
            "https://github.com/TheSuperHackers/GeneralsGameCode",
            "GeneralsGameCode",
        )
        pyside = link("https://www.qt.io/qt-for-python", "PySide6 (Qt for Python)")
        ea_source = link(
            "https://github.com/electronicarts/CnC_Generals_Zero_Hour",
            "open-sourcing the original game code under GPL-3.0",
        )

        return f"""
<h3>What is GenLauncher-LP?</h3>
<p>
GenLauncher-LP (Linux Port) is a <b>native Linux application</b> — written
in Python and PySide6, it runs directly on your system without Wine or Proton.
It manages mods, patches, and addons for <b>Command &amp; Conquer: Generals</b>
and <b>Zero Hour</b>, and launches the game through Steam/Proton.
</p>

<h3>Features</h3>
<ul>
<li>Browse and install mods, patches, and addons from the online catalog</li>
<li>S3-based multi-file downloads with delta/incremental updates</li>
<li>One-click community game patch (TheSuperHackers weekly builds)</li>
<li>Full Options.ini management (graphics, gamma, camera, scroll speed, FPS, and more)</li>
<li>Mod file integrity verification (SHA-256 checksums)</li>
<li>Star rating system with automatic sorting</li>
<li>Per-mod background images and theme colors</li>
<li>Symlink-based mod loading (no file copying)</li>
<li>Camera height patching via .big archive modification</li>
<li>DXVK/wined3d toggle for Proton</li>
<li>World Builder support</li>
<li>Crash detection and game process monitoring</li>
<li>Mod search — filter mods by name with instant partial matching</li>
<li>Game selector — switch between Generals and Zero Hour on the fly</li>
<li>Auto patch check — background version check with visual status</li>
<li>Custom download link overrides for broken catalog entries</li>
</ul>

<h3>Credits</h3>
<p>
<b>Created by Nele</b>, with the help of <b>Claude</b> (Anthropic) and
open-source code.
</p>

<h3>Special Thanks</h3>
<p>
<b>{rewritten}</b><br/>
The GenLauncher Rewritten project, successor to the original GenLauncher, is
the foundation of this port. The mod catalog, S3 infrastructure, and community
ecosystem all come from their work. Thank you for keeping the Generals modding
scene alive.
</p>
<p>
<b>{superhackers}</b><br/>
The community behind {superhackers_code} — weekly patched game builds that fix
bugs, improve stability, and add features to the 20-year-old engine. The
one-click "Patch Game" feature downloads their latest release directly.
</p>
<p>
<b>Electronic Arts</b><br/>
For {ea_source} in 2025, which made community patches and deep integrations
like this one possible.
</p>
<p>
<b>{pyside}</b><br/>
The Qt bindings for Python that power the entire user interface.
</p>

<h3>License</h3>
<p>GPL-3.0 — free and open-source software.</p>
"""
