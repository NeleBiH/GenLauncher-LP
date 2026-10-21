"""GameSelectionWindow — pick which C&C title the launcher should manage.

Mirrors Rewritten's ``LauncherGameSelectionWindow``. Shown at startup only when
more than one title is actually installed; with a single install there is
nothing to choose and the dialog never appears.

The catalog, the store layout, the launch target and the update check are all
keyed off this choice, so it has to be settled before the catalog loads.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
)

from ..core.models import GameInstall
from .theme import DEFAULT_COLORS, ui_font

GAME_LABELS = {
    "zh": "Command & Conquer: Generals - Zero Hour",
    "generals": "Command & Conquer: Generals",
}


def game_label(mode: str) -> str:
    """Human-readable name for a mode, falling back to the raw value."""
    return GAME_LABELS.get(mode, mode)


def distinct_modes(installs: list[GameInstall]) -> list[str]:
    """Modes present in ``installs``, in a stable display order."""
    seen: list[str] = []
    for install in installs:
        if install.mode not in seen:
            seen.append(install.mode)
    return seen


class GameSelectionWindow(QDialog):
    """List the installed titles and return the one the user wants to manage."""

    def __init__(
        self,
        installs: list[GameInstall],
        current: str = "zh",
        parent=None,
    ):
        super().__init__(parent)
        self._installs = installs
        self._mode = current if current in distinct_modes(installs) else (
            distinct_modes(installs)[0] if installs else current
        )

        self.setWindowTitle("Select game")
        self.setFixedSize(460, 320)
        self.setWindowFlags(
            Qt.WindowType.Dialog | Qt.WindowType.WindowCloseButtonHint
        )
        self._build_ui()

    def _build_ui(self) -> None:
        title = QLabel("Which game should GenLauncher manage?", self)
        title.setGeometry(20, 15, 420, 30)
        title.setFont(ui_font(13, bold=True))
        title.setWordWrap(True)
        title.setStyleSheet(f"color: {DEFAULT_COLORS.text};")

        self._list = QListWidget(self)
        self._list.setGeometry(20, 55, 420, 150)
        for install in self._installs:
            item = QListWidgetItem(game_label(install.mode))
            item.setData(Qt.ItemDataRole.UserRole, install.mode)
            self._list.addItem(item)
            if install.mode == self._mode:
                self._list.setCurrentItem(item)

        self._path = QLabel(self)
        self._path.setGeometry(20, 212, 420, 40)
        self._path.setWordWrap(True)
        self._path.setFont(ui_font(9))
        self._path.setStyleSheet(f"color: {DEFAULT_COLORS.text};")

        self._list.currentItemChanged.connect(self._show_path)
        self._show_path(self._list.currentItem(), None)

        ok_btn = QPushButton("Use this game", self)
        ok_btn.setGeometry(150, 265, 160, 35)
        ok_btn.setFont(ui_font(11))
        ok_btn.clicked.connect(self._on_accept)

    def _show_path(self, current, _previous) -> None:
        if current is None:
            self._path.setText("")
            return
        mode = current.data(Qt.ItemDataRole.UserRole)
        match = next((i for i in self._installs if i.mode == mode), None)
        self._path.setText(str(match.game_dir) if match else "")

    def _on_accept(self) -> None:
        item = self._list.currentItem()
        if item is None:
            return
        self._mode = item.data(Qt.ItemDataRole.UserRole)
        self.accept()

    @property
    def selected_mode(self) -> str:
        """The mode the user confirmed, valid even when the dialog is closed."""
        return self._mode
