"""ManualAddModificationWindow — add a mod from local files.

600×220 dialog: lets the user provide a name and version for locally-selected
archives.
"""

from __future__ import annotations

import re

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QPainter, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QLabel,
    QLineEdit,
    QPushButton,
)

from .info_dialog import InfoDialog
from .resources import ADDMOD_BG
from .theme import ui_font, DEFAULT_COLORS

# Allow word chars, dots, @, hyphens
_SANITIZE = re.compile(r"[^\w.@-]", re.UNICODE)


class ManualAddModWindow(QDialog):
    """Dialog for entering mod name + version when adding from local files."""

    confirmed = Signal(str, str)  # (mod_name, version)

    def __init__(self, files: list[str], parent=None):
        super().__init__(parent)
        self._files = files

        self.setWindowTitle("Enter Modification Name")
        self.setFixedSize(600, 220)
        self.setWindowFlags(
            Qt.WindowType.Dialog | Qt.WindowType.WindowCloseButtonHint
        )

        self._bg = QPixmap(ADDMOD_BG)
        self._build_ui()

    def paintEvent(self, event):
        if not self._bg.isNull():
            painter = QPainter(self)
            painter.drawPixmap(self.rect(), self._bg)
            painter.end()

    def _build_ui(self):
        title = QLabel("Input mod data", self)
        title.setGeometry(20, 10, 560, 30)
        title.setFont(ui_font(16, bold=True))
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet(f"color: {DEFAULT_COLORS.text};")

        name_label = QLabel("Modification name:", self)
        name_label.setGeometry(18, 50, 160, 22)
        name_label.setFont(ui_font(10))

        self._name_edit = QLineEdit(self)
        self._name_edit.setGeometry(18, 72, 564, 28)
        self._name_edit.setFont(ui_font(10))

        ver_label = QLabel("Version:", self)
        ver_label.setGeometry(18, 108, 160, 22)
        ver_label.setFont(ui_font(10))

        self._version_edit = QLineEdit(self)
        self._version_edit.setGeometry(18, 130, 564, 28)
        self._version_edit.setFont(ui_font(10))
        self._version_edit.setPlaceholderText("e.g. 1.0")

        btn_y = 172

        ok_btn = QPushButton("OK", self)
        ok_btn.setGeometry(150, btn_y, 120, 35)
        ok_btn.setFont(ui_font(11))
        ok_btn.clicked.connect(self._on_ok)

        cancel_btn = QPushButton("Cancel", self)
        cancel_btn.setGeometry(330, btn_y, 120, 35)
        cancel_btn.setFont(ui_font(11))
        cancel_btn.clicked.connect(self.reject)

    def _on_ok(self):
        name = self._name_edit.text().strip()
        version = self._version_edit.text().strip()

        if not name:
            InfoDialog.warning(self, "Error", "Modification name cannot be empty.")
            return
        if not version:
            InfoDialog.warning(self, "Error", "Version cannot be empty.")
            return
        if not any(c.isdigit() for c in version):
            InfoDialog.warning(self, "Error", "Version must contain at least one digit.")
            return

        # Sanitize
        name = _SANITIZE.sub("", name)
        version = _SANITIZE.sub("", version)

        if not name or not version:
            InfoDialog.warning(self, "Error", "Name or version contains only invalid characters.")
            return

        self.confirmed.emit(name, version)
        self.accept()

    @property
    def mod_name(self) -> str:
        return self._name_edit.text().strip()

    @property
    def version(self) -> str:
        return self._version_edit.text().strip()
