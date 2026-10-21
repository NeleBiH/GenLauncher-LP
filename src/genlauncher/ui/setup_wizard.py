"""First-run setup wizard.

Shown once when no settings file exists. Guides the user through
game detection, Proton selection, and basic preferences.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from ..core import steam as steam_mod
from ..core.config import Settings
from .theme import DEFAULT_COLORS, ui_font

log = logging.getLogger(__name__)

WELCOME_TEXT = (
    "Welcome to <b>GenLauncher-LP</b>!<br><br>"
    "This wizard will set up the launcher for first use.<br>"
    "Everything here can be changed later in Options."
)


class SetupWizard(QDialog):
    """Multi-step first-run wizard."""

    def __init__(self, settings: Settings, parent=None):
        super().__init__(parent)
        self._settings = settings
        c = DEFAULT_COLORS

        self.setWindowTitle("GenLauncher-LP — Setup")
        self.setFixedSize(520, 400)
        self.setStyleSheet(
            f"background-color: {c.dark_bg}; color: {c.text};"
        )

        self._layout = QVBoxLayout(self)
        self._layout.setSpacing(12)
        self._layout.setContentsMargins(24, 20, 24, 20)

        # Title
        title = QLabel("GenLauncher-LP")
        title.setFont(ui_font(18, bold=True))
        title.setStyleSheet(f"color: {c.active};")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._layout.addWidget(title)

        subtitle = QLabel("First-Run Setup")
        subtitle.setFont(ui_font(12))
        subtitle.setStyleSheet(f"color: {c.border};")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._layout.addWidget(subtitle)

        self._layout.addSpacing(8)

        # Welcome
        welcome = QLabel(WELCOME_TEXT)
        welcome.setFont(ui_font(10))
        welcome.setWordWrap(True)
        welcome.setTextFormat(Qt.TextFormat.RichText)
        self._layout.addWidget(welcome)

        self._layout.addSpacing(8)

        # Game detection
        self._installs = steam_mod.find_game_dirs()

        game_label = QLabel("Detected games:")
        game_label.setFont(ui_font(10, bold=True))
        game_label.setStyleSheet(f"color: {c.active};")
        self._layout.addWidget(game_label)

        if self._installs:
            game_names = {"zh": "Zero Hour", "generals": "Generals"}
            for inst in self._installs:
                row = QHBoxLayout()
                icon = QLabel("\u2714")  # checkmark
                icon.setStyleSheet("color: #4caf50;")
                icon.setFont(ui_font(12))
                icon.setFixedWidth(20)
                name = QLabel(f"{game_names.get(inst.mode, inst.mode)}")
                name.setFont(ui_font(10))
                path = QLabel(f"  {inst.game_dir}")
                path.setFont(ui_font(8))
                path.setStyleSheet("color: #888888;")
                row.addWidget(icon)
                row.addWidget(name)
                row.addWidget(path, 1)
                self._layout.addLayout(row)
        else:
            no_game = QLabel("No game installation found. Set the game path manually in Options.")
            no_game.setFont(ui_font(10))
            no_game.setStyleSheet("color: #ff9800;")
            no_game.setWordWrap(True)
            self._layout.addWidget(no_game)

        self._layout.addSpacing(8)

        # Proton detection
        builds = steam_mod.list_proton_builds()
        proton_label = QLabel("Proton:")
        proton_label.setFont(ui_font(10, bold=True))
        proton_label.setStyleSheet(f"color: {c.active};")
        self._layout.addWidget(proton_label)

        self._proton_combo = QComboBox()
        self._proton_combo.setFont(ui_font(10))
        self._proton_combo.setFixedHeight(28)
        self._proton_combo.addItem("Auto-detect (recommended)", "")
        for b in builds:
            self._proton_combo.addItem(b.name, b.name)
        self._layout.addWidget(self._proton_combo)

        self._layout.addStretch()

        # Buttons
        btn_row = QHBoxLayout()
        btn_style = (
            f"QPushButton {{ background: {c.dark_fill}; color: {c.text}; "
            f"border: 1px solid {c.border}; border-radius: 4px; padding: 6px 20px; }}"
            f"QPushButton:hover {{ background: {c.button_selection}; }}"
        )

        finish_btn = QPushButton("Start Launcher")
        finish_btn.setFont(ui_font(11, bold=True))
        finish_btn.setFixedHeight(36)
        finish_btn.setStyleSheet(
            f"QPushButton {{ background: {c.dark_fill}; color: {c.active}; "
            f"border: 2px solid {c.active}; border-radius: 4px; padding: 6px 20px; }}"
            f"QPushButton:hover {{ background: {c.button_selection}; }}"
        )
        finish_btn.clicked.connect(self._on_finish)

        btn_row.addStretch()
        btn_row.addWidget(finish_btn)
        self._layout.addLayout(btn_row)

    def _on_finish(self):
        proton = self._proton_combo.currentData() or ""
        self._settings.proton = proton

        # Set game to the first detected install
        if self._installs:
            modes = list({i.mode for i in self._installs})
            if "zh" in modes:
                self._settings.game = "zh"
            elif modes:
                self._settings.game = modes[0]

        self._settings.setup_done = True
        self._settings.save()
        log.info("setup wizard completed")
        self.accept()
