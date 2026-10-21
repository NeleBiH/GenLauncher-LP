"""AddModificationWindow — select a mod from the catalog to add.

368×469 dialog: shows uninstalled mods from the catalog so the user can pick
one to download/install.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QPainter, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
)

from .resources import ADDMOD_BG
from .theme import ui_font, DEFAULT_COLORS


class AddModWindow(QDialog):
    """Dialog for selecting a mod from the catalog to install."""

    mod_selected = Signal(str)  # mod_name

    def __init__(self, available_mods: list[str], parent=None):
        super().__init__(parent)
        self._selected: str = ""

        self.setWindowTitle("Select mod to add")
        self.setFixedSize(368, 469)
        self.setWindowFlags(
            Qt.WindowType.Dialog | Qt.WindowType.WindowCloseButtonHint
        )

        # Background
        self._bg = QPixmap(ADDMOD_BG)

        self._build_ui(available_mods)

    def paintEvent(self, event):
        if not self._bg.isNull():
            painter = QPainter(self)
            painter.drawPixmap(self.rect(), self._bg)
            painter.end()

    def _build_ui(self, mods: list[str]):
        title = QLabel("Select mod", self)
        title.setGeometry(20, 15, 328, 35)
        title.setFont(ui_font(14, bold=True))
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet(f"color: {DEFAULT_COLORS.text};")

        self._mod_list = QListWidget(self)
        self._mod_list.setGeometry(20, 55, 328, 340)
        for name in sorted(mods):
            item = QListWidgetItem(name)
            self._mod_list.addItem(item)

        btn_y = 410

        add_btn = QPushButton("Add", self)
        add_btn.setGeometry(50, btn_y, 120, 35)
        add_btn.setFont(ui_font(11))
        add_btn.clicked.connect(self._on_add)

        cancel_btn = QPushButton("Cancel", self)
        cancel_btn.setGeometry(198, btn_y, 120, 35)
        cancel_btn.setFont(ui_font(11))
        cancel_btn.clicked.connect(self.reject)

    def _on_add(self):
        item = self._mod_list.currentItem()
        if item:
            self._selected = item.text()
            self.mod_selected.emit(self._selected)
            self.accept()

    @property
    def selected_mod(self) -> str:
        return self._selected
