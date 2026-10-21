"""Mod Info Dialog — shows details about a selected mod.

Displays mod name, version, type, install status, and links (Discord,
ModDB, news, support). Opened by double-clicking a mod in the list.
"""

from __future__ import annotations

import webbrowser
from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from ..core.models import ModificationVersion, ModType
from .theme import DEFAULT_COLORS, ui_font


MOD_TYPE_LABELS = {
    ModType.MOD: "Mod",
    ModType.PATCH: "Patch",
    ModType.ADDON: "Addon",
    ModType.EXECUTABLE: "Executable",
}


class ModInfoDialog(QDialog):
    """Shows detailed information about a mod/patch/addon."""

    def __init__(self, mv: ModificationVersion, parent=None):
        super().__init__(parent)
        self._mv = mv
        c = DEFAULT_COLORS

        self.setWindowTitle(f"Mod Info — {mv.name}")
        self.setFixedSize(460, 380)
        self.setStyleSheet(
            f"background-color: {c.dark_bg}; color: {c.text};"
        )

        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.setContentsMargins(20, 16, 20, 16)

        # Title
        title = QLabel(mv.name)
        title.setFont(ui_font(16, bold=True))
        title.setStyleSheet(f"color: {c.active};")
        title.setWordWrap(True)
        layout.addWidget(title)

        # Details grid
        details = []
        details.append(("Version", mv.version or "—"))
        details.append(("Type", MOD_TYPE_LABELS.get(mv.mod_type, str(mv.mod_type))))

        if mv.dependence_name:
            details.append(("Requires", mv.dependence_name))

        status = "Installed" if mv.installed else "Not installed"
        if mv.installed and mv.installed_at:
            dt = datetime.fromtimestamp(mv.installed_at)
            status += f"  ({dt.strftime('%Y-%m-%d')})"
        details.append(("Status", status))

        if mv.network_info:
            details.append(("Network", mv.network_info))
        if mv.executable_file_name:
            details.append(("Executable", mv.executable_file_name))
        if mv.deprecated:
            details.append(("Note", "This version is deprecated"))

        for label_text, value_text in details:
            row = QHBoxLayout()
            lbl = QLabel(f"{label_text}:")
            lbl.setFont(ui_font(10, bold=True))
            lbl.setFixedWidth(90)
            lbl.setStyleSheet(f"color: {c.border};")
            val = QLabel(value_text)
            val.setFont(ui_font(10))
            val.setWordWrap(True)

            if label_text == "Status":
                color = "#4caf50" if mv.installed else "#ff9800"
                val.setStyleSheet(f"color: {color};")

            row.addWidget(lbl)
            row.addWidget(val, 1)
            layout.addLayout(row)

        layout.addSpacing(8)

        # Links
        links = []
        if mv.moddb_link:
            links.append(("ModDB", mv.moddb_link))
        if mv.discord_link:
            links.append(("Discord", mv.discord_link))
        if mv.news_link:
            links.append(("News", mv.news_link))
        if mv.support_link:
            links.append(("Support", mv.support_link))

        if links:
            links_label = QLabel("Links")
            links_label.setFont(ui_font(11, bold=True))
            links_label.setStyleSheet(f"color: {c.active};")
            layout.addWidget(links_label)

            links_row = QHBoxLayout()
            for text, url in links:
                btn = QPushButton(text)
                btn.setFont(ui_font(9))
                btn.setFixedHeight(28)
                btn.setCursor(Qt.CursorShape.PointingHandCursor)
                btn.setStyleSheet(
                    f"QPushButton {{ background: {c.dark_fill}; color: {c.text}; "
                    f"border: 1px solid {c.border}; border-radius: 4px; padding: 4px 12px; }}"
                    f"QPushButton:hover {{ background: {c.button_selection}; }}"
                )
                btn.clicked.connect(lambda _, u=url: webbrowser.open(u))
                links_row.addWidget(btn)
            links_row.addStretch()
            layout.addLayout(links_row)

        layout.addStretch()

        # Close button
        close_btn = QPushButton("Close")
        close_btn.setFont(ui_font(10))
        close_btn.setFixedHeight(32)
        close_btn.setStyleSheet(
            f"QPushButton {{ background: {c.dark_fill}; color: {c.text}; "
            f"border: 1px solid {c.border}; border-radius: 4px; }}"
            f"QPushButton:hover {{ background: {c.button_selection}; }}"
        )
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn)
