"""Options/settings dialog.

986×762 frameless dialog with game graphics options, camera height,
resolution, and launcher preferences.
"""

from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger("genlauncher.ui")

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QSlider,
    QWidget,
)

from ..core.config import Settings
from ..core.options_ini import (
    STARTING_CASH_PRESETS,
    apply_gfx_settings,
    apply_numeric_settings,
    ensure_options,
    find_options_ini,
    find_skirmish_ini,
    get_starting_cash,
    gfx_from_options,
    numeric_from_options,
    read_skirmish,
    set_starting_cash,
    write_options,
    write_skirmish,
)
from .info_dialog import InfoDialog
from .theme import ui_font, DEFAULT_COLORS


class OptionsWindow(QDialog):
    """Options dialog."""

    def __init__(self, settings: Settings, parent=None):
        super().__init__(parent)
        self._settings = settings
        self._ini_path = find_options_ini(game=settings.game)
        self._game_options: dict[str, str] = {}
        if self._ini_path:
            self._game_options = ensure_options(self._ini_path)
        self._skirmish_path = find_skirmish_ini(game=settings.game)
        self._skirmish_options: dict[str, str] = {}
        if self._skirmish_path:
            self._skirmish_options = read_skirmish(self._skirmish_path)
        self._setup_window()
        self._build_ui()
        self._load_state()

    def _setup_window(self):
        self.setWindowTitle("Options")
        self.setFixedSize(986, 762)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog
        )
    def paintEvent(self, event):
        painter = QPainter(self)
        # Solid dark background
        painter.setBrush(QColor(DEFAULT_COLORS.dark_bg))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRect(self.rect())
        # Inner panel
        painter.setBrush(QColor(0, 0, 0, 216))
        painter.drawRect(31, 38, 925, 688)
        # Border
        painter.setPen(QPen(QColor(DEFAULT_COLORS.dark_fill), 4))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(31, 38, 925, 688)
        painter.end()

    def _build_ui(self):
        c = DEFAULT_COLORS
        lx = 74   # left panel x (col 1 start)
        rx = 510  # right panel x (col 3 start)
        rw = 430  # right panel widget width (fits within panel edge)

        # ---- Close button (needed for frameless) ----
        close_btn = QPushButton("X", self)
        close_btn.setGeometry(940, 40, 30, 30)
        close_btn.setFont(ui_font(12, bold=True))
        close_btn.clicked.connect(self.reject)

        # ---- Header ----
        header = QLabel("OPTIONS", self)
        header.setGeometry(lx, 42, 400, 50)
        header.setFont(ui_font(16, bold=True))
        header.setStyleSheet(f"color: {c.text};")

        # ---- Left panel ----
        y = 100

        # Camera height
        QLabel("Camera Height", self).setGeometry(lx, y, 200, 20)
        y += 24

        self.camera_default = QRadioButton("Use default camera height (recommended)", self)
        self.camera_default.setGeometry(lx, y, 400, 20)
        y += 22

        self.camera_custom = QRadioButton("Use custom camera height", self)
        self.camera_custom.setGeometry(lx, y, 400, 20)
        self.camera_custom.toggled.connect(self._on_camera_toggle)
        y += 24

        # Explicit group so camera radios don't interfere with GenTool radios
        self._camera_group = QButtonGroup(self)
        self._camera_group.addButton(self.camera_default)
        self._camera_group.addButton(self.camera_custom)

        self.camera_slider = QSlider(Qt.Orientation.Horizontal, self)
        self.camera_slider.setGeometry(lx, y, 285, 25)
        self.camera_slider.setMinimum(310)
        self.camera_slider.setMaximum(850)
        self.camera_slider.setTickInterval(10)
        self.camera_slider.valueChanged.connect(self._on_camera_value)

        self.camera_value_label = QLabel("310", self)
        self.camera_value_label.setGeometry(lx + 300, y, 60, 25)
        self.camera_value_label.setFont(ui_font(10))
        y += 34

        # Executable management — shows what is actually in the game folder.
        self.exe_summary = QLabel(self)
        self.exe_summary.setGeometry(lx, y, 410, 18)
        self.exe_summary.setFont(ui_font(8))
        self.exe_summary.setStyleSheet(f"color: {c.inactive_border};")
        self.exe_summary.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        y += 20

        self.manage_game_exes = QPushButton("Manage game clients…", self)
        self.manage_game_exes.setGeometry(lx, y, 198, 30)
        self.manage_game_exes.setFont(ui_font(9))
        self.manage_game_exes.clicked.connect(
            lambda: self._manage_executables("game")
        )

        self.manage_wb_exes = QPushButton("Manage World Builders…", self)
        self.manage_wb_exes.setGeometry(lx + 208, y, 198, 30)
        self.manage_wb_exes.setFont(ui_font(9))
        self.manage_wb_exes.clicked.connect(
            lambda: self._manage_executables("world_builder")
        )
        y += 32

        # GenTool (disabled on Linux — Phase 4)
        self.install_gentool = QRadioButton("Install and autoupdate Gentool (N/A on Linux)", self)
        self.install_gentool.setGeometry(lx, y, 420, 20)
        y += 22

        self.disable_gentool = QRadioButton("Disable Gentool", self)
        self.disable_gentool.setGeometry(lx, y, 400, 20)
        y += 28

        self._gentool_group = QButtonGroup(self)
        self._gentool_group.addButton(self.install_gentool)
        self._gentool_group.addButton(self.disable_gentool)

        # Auto delete
        self.auto_delete = QCheckBox("Auto delete outdated modification versions", self)
        self.auto_delete.setGeometry(lx, y, 400, 20)
        y += 26

        # Additional params
        QLabel("Additional params for game to start:", self).setGeometry(lx, y, 350, 20)
        y += 22

        self.game_params = QLineEdit(self)
        self.game_params.setGeometry(lx, y, 380, 27)
        y += 34

        # Resolution
        QLabel("Resolution:", self).setGeometry(lx, y, 100, 20)
        self.resolution_combo = QComboBox(self)
        self.resolution_combo.setGeometry(lx, y + 22, 142, 28)
        self._populate_resolutions()

        # Starting cash (right of resolution)
        QLabel("Skirmish starting cash:", self).setGeometry(lx + 200, y, 200, 20)
        self.cash_combo = QComboBox(self)
        self.cash_combo.setGeometry(lx + 200, y + 22, 142, 28)
        self.cash_combo.setEditable(True)
        for preset in STARTING_CASH_PRESETS:
            self.cash_combo.addItem(f"{preset:,}".replace(",", " "), preset)
        if not self._skirmish_path:
            self.cash_combo.setEnabled(False)
            self.cash_combo.setToolTip("Skirmish.ini not found — run the game once first")
        y += 52

        # Max particle count
        QLabel("Maximum particle count:", self).setGeometry(lx, y, 200, 20)
        y += 22

        self.particle_slider = QSlider(Qt.Orientation.Horizontal, self)
        self.particle_slider.setGeometry(lx, y, 300, 25)
        self.particle_slider.setMinimum(100)
        self.particle_slider.setMaximum(50000)
        self.particle_slider.setTickInterval(20)
        self.particle_slider.valueChanged.connect(
            lambda v: self.particle_label.setText(str(v))
        )

        self.particle_label = QLabel("2500", self)
        self.particle_label.setGeometry(lx + 310, y, 70, 25)
        self.particle_label.setFont(ui_font(10))
        y += 32

        # Texture quality
        QLabel("Texture quality level:", self).setGeometry(lx, y, 200, 20)
        y += 22

        self.texture_slider = QSlider(Qt.Orientation.Horizontal, self)
        self.texture_slider.setGeometry(lx, y, 118, 25)
        self.texture_slider.setMinimum(0)
        self.texture_slider.setMaximum(2)
        self.texture_slider.setTickInterval(1)
        self.texture_slider.valueChanged.connect(
            lambda v: self.texture_label.setText(str(v))
        )

        self.texture_label = QLabel("0", self)
        self.texture_label.setGeometry(lx + 130, y, 48, 25)
        self.texture_label.setFont(ui_font(10))

        lq = QLabel("Lower - better performance, higher - better graphics quality", self)
        lq.setGeometry(lx, y + 28, 400, 16)
        lq.setFont(ui_font(8))
        lq.setStyleSheet(f"color: {c.inactive_border};")
        y += 50

        # Gamma (brightness)
        QLabel("Gamma:", self).setGeometry(lx, y, 80, 25)
        self.gamma_slider = QSlider(Qt.Orientation.Horizontal, self)
        self.gamma_slider.setGeometry(lx + 85, y, 200, 25)
        self.gamma_slider.setMinimum(50)
        self.gamma_slider.setMaximum(100)
        self.gamma_slider.valueChanged.connect(
            lambda v: self.gamma_label.setText(str(v))
        )
        self.gamma_label = QLabel("50", self)
        self.gamma_label.setGeometry(lx + 295, y, 50, 25)
        self.gamma_label.setFont(ui_font(10))
        y += 28

        # Scroll speed
        QLabel("Scroll speed:", self).setGeometry(lx, y, 100, 25)
        self.scroll_slider = QSlider(Qt.Orientation.Horizontal, self)
        self.scroll_slider.setGeometry(lx + 105, y, 180, 25)
        self.scroll_slider.setMinimum(0)
        self.scroll_slider.setMaximum(100)
        self.scroll_slider.valueChanged.connect(
            lambda v: self.scroll_label.setText(str(v))
        )
        self.scroll_label = QLabel("50", self)
        self.scroll_label.setGeometry(lx + 295, y, 50, 25)
        self.scroll_label.setFont(ui_font(10))
        y += 28

        # Detected INI paths
        ini_status = "Options.ini: " + (str(self._ini_path) if self._ini_path else "not found")
        skirmish_status = "Skirmish.ini: " + (str(self._skirmish_path) if self._skirmish_path else "not found")
        ini_label = QLabel(ini_status, self)
        ini_label.setGeometry(lx, y, 420, 14)
        ini_label.setFont(ui_font(7))
        ini_label.setStyleSheet(f"color: {c.inactive_border};")
        ini_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        y += 14
        skirmish_label = QLabel(skirmish_status, self)
        skirmish_label.setGeometry(lx, y, 420, 14)
        skirmish_label.setFont(ui_font(7))
        skirmish_label.setStyleSheet(f"color: {c.inactive_border};")
        skirmish_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        y += 20

        # ---- Right panel ----
        ry = 100

        # Graphics toggles
        self._gfx_checks: dict[str, QCheckBox] = {}
        gfx_options = [
            ("3d_shadows", "3D Shadows"),
            ("2d_shadows", "2D Shadows"),
            ("show_props", "Show Props"),
            ("cloud_shadows", "Cloud Shadows"),
            ("extra_animation", "Extra Animation"),
            ("extra_ground_lighting", "Extra Ground Lighting"),
            ("disable_lod", "Disable Dynamic Level Of Detail"),
            ("smooth_water", "Smooth Water Borders"),
            ("heat_effects", "Heat Effects (recommended to turn off)"),
            ("display_units", "Display Units Through Buildings"),
            ("show_trees", "Show Trees"),
            ("anti_aliasing", "Anti-Aliasing"),
            ("retaliation", "Automatic Retaliation"),
            ("use_double_click_attack", "Double-Click Attack Move"),
            ("use_alternate_mouse", "Alternate Mouse Setup"),
            ("fps_limit", "FPS Limit (30fps cap)"),
            ("cursor_capture", "Capture Cursor in Window"),
        ]

        for key, label in gfx_options:
            cb = QCheckBox(label, self)
            cb.setGeometry(rx, ry, rw, 22)
            self._gfx_checks[key] = cb
            ry += 24

        ry += 10

        # Check mod files
        self.check_mod_files = QCheckBox("Check mod files integrity", self)
        self.check_mod_files.setGeometry(rx, ry, rw, 22)
        ry += 22

        check_note = QLabel("Works only for the latest version of mods.", self)
        check_note.setGeometry(rx + 24, ry, rw, 14)
        check_note.setFont(ui_font(7))
        check_note.setStyleSheet(f"color: {c.inactive_border};")
        ry += 16

        warn = QLabel("WARNING! This check may take a long time before starting the game!", self)
        warn.setGeometry(rx + 24, ry, rw, 14)
        warn.setFont(ui_font(7))
        warn.setStyleSheet(f"color: {c.inactive_border};")
        ry += 18

        self.ask_before_check = QCheckBox("Ask before checking mod files integrity", self)
        self.ask_before_check.setGeometry(rx, ry, rw, 22)
        ry += 24

        self.hide_launcher = QCheckBox("Hide GenLauncher while the game is running", self)
        self.hide_launcher.setGeometry(rx, ry, rw, 22)
        ry += 28

        # Vulkan / DXVK toggle
        self.vulkan_check = QCheckBox("Use DXVK (Vulkan) — recommended", self)
        self.vulkan_check.setGeometry(rx, ry, rw, 22)
        ry += 22

        vulkan_note = QLabel(
            "Checked = DXVK translates DirectX to Vulkan (default Proton).\n"
            "Unchecked = forces wined3d (OpenGL). Try this if you have\n"
            "graphical glitches or crashes with DXVK.",
            self,
        )
        vulkan_note.setGeometry(rx + 24, ry, rw, 42)
        vulkan_note.setFont(ui_font(7))
        vulkan_note.setStyleSheet(f"color: {c.inactive_border};")

        # ---- Bottom buttons ----
        btn_y = 690

        self.apply_btn = QPushButton("Apply", self)
        self.apply_btn.setGeometry(lx, btn_y, 156, 38)
        self.apply_btn.setFont(ui_font(14))
        self.apply_btn.clicked.connect(self._on_apply)

        self.recommended_btn = QPushButton("Set recommended settings", self)
        self.recommended_btn.setGeometry(rx, btn_y, 394, 38)
        self.recommended_btn.setFont(ui_font(14))
        self.recommended_btn.clicked.connect(self._on_set_recommended)

    # ---- state management ---------------------------------------------------

    def _load_state(self):
        s = self._settings

        # --- Load from Options.ini if available ---
        log.info("Options load: ini_path=%s has_options=%s", self._ini_path, bool(self._game_options))
        if self._game_options:
            nums = numeric_from_options(self._game_options)
            gfx = gfx_from_options(self._game_options)
            log.info("Options load: resolution=%r camera=%r particles=%r texture=%r",
                     nums.get("resolution"), nums.get("camera_height"),
                     nums.get("particle_count"), nums.get("texture_slider"))

            # Camera
            cam = nums.get("camera_height", 0)
            if cam > 0:
                self.camera_custom.setChecked(True)
                self.camera_slider.setValue(cam)
                self.camera_slider.setEnabled(True)
            else:
                self.camera_default.setChecked(True)
                self.camera_slider.setValue(310)
                self.camera_slider.setEnabled(False)

            # Resolution
            res = str(nums.get("resolution", "1024 768"))
            idx = self.resolution_combo.findText(res)
            if idx >= 0:
                self.resolution_combo.setCurrentIndex(idx)

            # Particles
            self.particle_slider.setValue(nums.get("particle_count", 2500))

            # Texture
            self.texture_slider.setValue(nums.get("texture_slider", 0))

            # Gamma, scroll
            self.gamma_slider.setValue(nums.get("gamma", 50))
            self.scroll_slider.setValue(nums.get("scroll_factor", 50))

            # Graphics checkboxes
            for key, checked in gfx.items():
                if key in self._gfx_checks:
                    self._gfx_checks[key].setChecked(checked)
        else:
            # Fallback: load from launcher settings
            if s.camera_height > 0:
                self.camera_custom.setChecked(True)
                self.camera_slider.setValue(s.camera_height)
                self.camera_slider.setEnabled(True)
            else:
                self.camera_default.setChecked(True)
                self.camera_slider.setValue(310)
                self.camera_slider.setEnabled(False)

        self.camera_value_label.setText(str(self.camera_slider.value()))

        # Starting cash
        if self._skirmish_options:
            cash = get_starting_cash(self._skirmish_options)
            idx = self.cash_combo.findData(cash)
            if idx >= 0:
                self.cash_combo.setCurrentIndex(idx)
            else:
                self.cash_combo.setEditText(str(cash))

        self._refresh_exe_summary()

        # GenTool — always disabled for Linux port (Phase 4)
        self.disable_gentool.setChecked(True)
        self.install_gentool.setEnabled(False)
        self.disable_gentool.setEnabled(False)

        # Auto delete
        self.auto_delete.setChecked(s.auto_delete_old_versions)

        # Game params
        self.game_params.setText(s.extra_args)

        # Mod checks
        self.check_mod_files.setChecked(s.check_mod_files)
        self.ask_before_check.setChecked(s.ask_before_check)
        self.hide_launcher.setChecked(s.hide_launcher)

        # Vulkan/DXVK
        self.vulkan_check.setChecked(s.use_vulkan)

    def _save_state(self):
        s = self._settings

        # Camera
        if self.camera_custom.isChecked():
            s.camera_height = self.camera_slider.value()
        else:
            s.camera_height = 0

        # Auto delete
        s.auto_delete_old_versions = self.auto_delete.isChecked()

        # Game params
        s.extra_args = self.game_params.text().strip()

        # Checks
        s.check_mod_files = self.check_mod_files.isChecked()
        s.ask_before_check = self.ask_before_check.isChecked()
        s.hide_launcher = self.hide_launcher.isChecked()

        # Vulkan/DXVK
        s.use_vulkan = self.vulkan_check.isChecked()

        s.save()

        # --- Write to Options.ini ---
        if self._ini_path:
            # Gather graphics checkbox states
            gfx_states = {
                key: cb.isChecked() for key, cb in self._gfx_checks.items()
            }
            apply_gfx_settings(self._game_options, gfx_states)

            res_text = self.resolution_combo.currentText()
            log.info(
                "Options save: resolution=%r camera=%d particles=%d texture=%d ini=%s",
                res_text, s.camera_height, self.particle_slider.value(),
                self.texture_slider.value(), self._ini_path,
            )

            apply_numeric_settings(
                self._game_options,
                resolution=res_text,
                camera_height=s.camera_height,
                particle_count=self.particle_slider.value(),
                texture_slider=self.texture_slider.value(),
                gamma=self.gamma_slider.value(),
                scroll_factor=self.scroll_slider.value(),
            )

            write_options(self._ini_path, self._game_options)
        else:
            log.warning("Options save: no Options.ini found, skipping INI write")

        # --- Write to Skirmish.ini ---
        if self._skirmish_path and self._skirmish_options:
            cash_text = self.cash_combo.currentText().replace(" ", "")
            try:
                cash = int(cash_text)
                if cash > 0:
                    set_starting_cash(self._skirmish_options, cash)
                    write_skirmish(self._skirmish_path, self._skirmish_options)
            except ValueError:
                pass

    def _on_apply(self):
        self._save_state()
        self.accept()

    # ---- executable management ---------------------------------------------

    def _resolve_game_dir(self) -> Path | None:
        """Where the executables live, using the same precedence as launching."""
        from ..core import steam as steam_mod

        if self._settings.game_dir:
            override = Path(self._settings.game_dir).expanduser()
            return override if override.is_dir() else None
        installs = steam_mod.find_game_dirs()
        chosen = steam_mod.pick_install(installs, self._settings.game)
        return chosen.game_dir if chosen else None

    def _selected_exe(self, target: str) -> tuple[str, list]:
        if target == "game":
            return self._settings.game_exe, self._settings.custom_game_exes
        return self._settings.world_builder_exe, self._settings.custom_world_builder_exes

    def _exe_label(self, target: str, game_dir: Path) -> str:
        from ..core import executables as exe

        selected, custom = self._selected_exe(target)
        if not selected:
            return "launcher default"
        entries = exe.discover(game_dir, self._settings.game, target, custom)
        entry = next(
            (e for e in entries if e.path.casefold() == selected.casefold()), None
        )
        if entry is None:
            return f"{selected} (not in the list)"
        return entry.label if entry.available else f"{entry.label} — missing"

    def _refresh_exe_summary(self) -> None:
        game_dir = self._resolve_game_dir()
        if game_dir is None:
            self.exe_summary.setText("No game install found — executables cannot be listed")
            self.manage_game_exes.setEnabled(False)
            self.manage_wb_exes.setEnabled(False)
            return
        self.manage_game_exes.setEnabled(True)
        self.manage_wb_exes.setEnabled(True)
        self.exe_summary.setText(
            "Game executable: %s   ·   World Builder: %s"
            % (self._exe_label("game", game_dir), self._exe_label("world_builder", game_dir))
        )

    def _manage_executables(self, target: str) -> None:
        from .executable_management_window import ExecutableManagementWindow

        game_dir = self._resolve_game_dir()
        if game_dir is None:
            InfoDialog.warning(
                self,
                "Game not found",
                "No C&C Generals / Zero Hour install found.\n"
                "Set the game path in Options first.",
            )
            return

        selected_key, custom_key = self._settings_keys(target)
        window = ExecutableManagementWindow(
            game_dir,
            self._settings.game,
            target,
            custom=getattr(self._settings, custom_key),
            selected_path=getattr(self._settings, selected_key),
            parent=self,
        )
        if window.exec() != int(QDialog.DialogCode.Accepted):
            return
        # Saved straight away: this dialog has its own confirm button, so
        # cancelling Options afterwards must not undo an explicit choice.
        setattr(self._settings, custom_key, window.custom)
        setattr(self._settings, selected_key, window.selected_path)
        self._settings.save()
        self._refresh_exe_summary()

    @staticmethod
    def _settings_keys(target: str) -> tuple[str, str]:
        if target == "game":
            return "game_exe", "custom_game_exes"
        return "world_builder_exe", "custom_world_builder_exes"

    def _on_set_recommended(self):
        """Apply recommended defaults (mirrors ApplyDefaultGameOptions)."""
        self.camera_default.setChecked(True)
        self.camera_slider.setValue(310)
        self.auto_delete.setChecked(False)
        self.particle_slider.setValue(2500)
        self.texture_slider.setValue(1)  # Medium (INI TextureReduction=1)
        self.gamma_slider.setValue(50)
        self.scroll_slider.setValue(50)
        self.check_mod_files.setChecked(False)
        self.ask_before_check.setChecked(True)
        self.hide_launcher.setChecked(False)

        # Graphics recommended defaults
        recommended = {
            "3d_shadows": True,
            "2d_shadows": True,
            "show_props": True,
            "cloud_shadows": False,
            "extra_animation": True,
            "extra_ground_lighting": True,
            "disable_lod": False,      # DynamicLOD = yes
            "smooth_water": True,
            "heat_effects": False,
            "display_units": True,
            "show_trees": True,
            "anti_aliasing": False,
            "retaliation": True,
            "use_double_click_attack": False,
            "use_alternate_mouse": False,
            "fps_limit": True,
            "cursor_capture": True,
        }
        for key, val in recommended.items():
            if key in self._gfx_checks:
                self._gfx_checks[key].setChecked(val)

        self.vulkan_check.setChecked(True)

    def _on_camera_toggle(self, checked: bool):
        self.camera_slider.setEnabled(checked)

    def _on_camera_value(self, value: int):
        self.camera_value_label.setText(str(value))

    def _populate_resolutions(self):
        """Add common resolutions to the dropdown."""
        try:
            from PySide6.QtWidgets import QApplication
            screen = QApplication.primaryScreen()
            if screen:
                geo = screen.geometry()
                native = f"{geo.width()} {geo.height()}"
            else:
                native = "1920 1080"
        except Exception:
            native = "1920 1080"

        resolutions = [
            native,
            "3840 2160",
            "2560 1440",
            "1920 1080",
            "1680 1050",
            "1600 900",
            "1440 900",
            "1366 768",
            "1280 1024",
            "1280 800",
            "1280 720",
            "1024 768",
            "800 600",
            "640 480",
        ]
        # Deduplicate while preserving order
        seen = set()
        for r in resolutions:
            if r not in seen:
                self.resolution_combo.addItem(r)
                seen.add(r)

    # Allow dragging the frameless window
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if hasattr(self, '_drag_pos') and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()
