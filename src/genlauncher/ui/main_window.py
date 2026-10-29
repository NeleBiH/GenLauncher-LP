"""Main launcher window.

Resizable (minimum 986×762) with:
  - Tab buttons (Mods / Patches / Addons)
  - Mod list (left, scrollable — stretches on resize)
  - Right panel (Launch, WorldBuilder, Windowed, QuickStart, Options, Exit)
  - Version info and launcher-update progress (right, bottom)
  - Download spinner (top-right, visible during downloads)
  - Memorial text (bottom-left)
"""

from __future__ import annotations

import logging
import os
import webbrowser
from pathlib import Path

from PySide6.QtCore import QMetaObject, QSize, Slot, Qt, QTimer, Q_ARG
from PySide6.QtGui import (
    QBrush,
    QFont,
    QIcon,
    QPainter,
    QPixmap,
)
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..core.catalog import CatalogLoadResult
from ..core.config import GAME_FILES, Paths, Settings
from ..core.downloader import Progress
from ..core.link_overrides import LinkOverrides
from ..core.linker import LinkStrategy
from ..core.models import Catalog, ModificationVersion, ModType, RawModEntry
from ..core.store import ModStore
from .info_dialog import InfoDialog
from .integrity_review_dialog import IntegrityReviewDialog
from .mod_widgets import AddonItemWidget, ModItemWidget, make_blink_animation
from .resources import BACKGROUND_GENERALS, BACKGROUND_ZH
from .theme import ui_font, DEFAULT_COLORS, Colors, colors_from_mod, generate_qss
from .workers import (
    CatalogWorker,
    DownloadInstallWorker,
    GamePatchWorker,
    GameRunner,
    ImageLoader,
    IntegrityWorker,
    LauncherUpdateCheckWorker,
    LinkWorker,
    S3DownloadInstallWorker,
    UnlinkWorker,
)

log = logging.getLogger("genlauncher.ui")


class MainWindow(QWidget):
    """The main GenLauncher window (minimum 986×762, resizable)."""

    def __init__(
        self,
        paths: Paths,
        settings: Settings,
        catalog_result: CatalogLoadResult | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self._paths = paths
        self._settings = settings
        self._store = ModStore(paths.data_dir)
        self._link_overrides = LinkOverrides(paths.data_dir)
        self._catalog: Catalog | None = None
        self._catalog_mods: dict[str, ModificationVersion] = {}
        self._catalog_patches: list[ModificationVersion] = []
        self._catalog_addons: list[ModificationVersion] = []
        self._catalog_exes: list[ModificationVersion] = []
        self._selected_mod: RawModEntry | None = None
        self._active_downloads: dict[str, DownloadInstallWorker | S3DownloadInstallWorker] = {}
        self._game_runner: GameRunner | None = None
        self._image_loader: ImageLoader | None = None
        self._is_game_running = False
        self._current_colors: Colors = DEFAULT_COLORS
        self._selected_versions: dict[str, str] = {}  # mod_name -> selected version
        # .big whose MaxCameraHeight we patched and still owe a restore for.
        # Tracked on self so every exit path can put the game file back.
        self._pending_camera_big: Path | None = None
        self._integrity_worker: IntegrityWorker | None = None
        self._patch_worker: GamePatchWorker | None = None

        self._setup_window()
        self._build_ui()

        if catalog_result and catalog_result.catalog:
            self._catalog = catalog_result.catalog
            self._show_warnings(catalog_result)

        self._refresh_mod_list()

    # ---- window setup -------------------------------------------------------

    def _setup_window(self):
        self.setWindowTitle("GenLauncher-LP")
        self.setMinimumSize(986, 762)
        self.resize(986, 762)

        # Background image
        game = self._settings.game
        bg_path = BACKGROUND_GENERALS if game == "generals" else BACKGROUND_ZH
        self._bg_pixmap = QPixmap(bg_path)

    def paintEvent(self, event):
        if not self._bg_pixmap.isNull():
            painter = QPainter(self)
            painter.drawPixmap(self.rect(), self._bg_pixmap)
            painter.end()

    def resizeEvent(self, event):
        super().resizeEvent(event)

    # ---- catalog loading ----------------------------------------------------

    def start_catalog_load(self):
        """Kick off a background catalog fetch.  Shows a loading bar and
        disables the UI until the catalog arrives."""
        self._set_ui_enabled(False)
        self._loading_bar.show()
        self.version_label.hide()

        self._catalog_worker = CatalogWorker(
            cache_dir=self._paths.cache_dir,
            game=self._settings.game,
            ttl=self._settings.catalog_ttl,
            link_overrides=self._link_overrides,
            parent=self,
        )
        self._catalog_worker.finished.connect(self._on_catalog_loaded)
        self._catalog_worker.error.connect(self._on_catalog_error)
        self._catalog_worker.start()

    def _on_catalog_loaded(self, catalog_result):
        self._loading_bar.hide()
        self.version_label.show()
        self._set_ui_enabled(True)

        cat_load = catalog_result.load_result
        if cat_load and cat_load.catalog:
            self._catalog = cat_load.catalog
            self._show_warnings(cat_load)
            self.set_catalog_data(
                cat_load.catalog,
                catalog_result.mods,
                catalog_result.patches,
                catalog_result.addons,
                catalog_result.exes,
            )

        self._check_patch_version()
        self._check_launcher_version()

    def _on_catalog_error(self, message: str):
        log.error("Catalog error: %s", message)
        self._loading_bar.hide()
        self.version_label.show()
        self._set_ui_enabled(True)
        self._refresh_mod_list()
        InfoDialog.warning(
            self,
            "Catalog Error",
            f"Could not load mod catalog:\n{message}\n\n"
            "You can still use installed mods.",
        )

    def _set_ui_enabled(self, enabled: bool):
        """Enable or disable the main UI while catalog loads."""
        for btn in (
            self.launch_btn, self.wb_btn, self.windowed_btn,
            self.quickstart_btn, self.options_btn,
            self.mods_btn, self.patches_btn, self.addons_btn,
            self.exes_btn, self.add_mod_btn, self.add_from_files_btn,
            self.patch_game_btn, self.about_btn,
        ):
            btn.setEnabled(enabled)
        for child in self.findChildren(QListWidget):
            child.setEnabled(enabled)

    # ---- build UI -----------------------------------------------------------

    def _build_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(15, 15, 15, 8)
        main_layout.setSpacing(6)

        # ---- Main content: left panel (mod list) + right panel (buttons) ----
        content_row = QHBoxLayout()
        content_row.setSpacing(10)

        # == Left panel: QFrame with cyan border containing mod list ==
        left_frame = QFrame()
        left_frame.setObjectName("leftPanel")
        left_frame.setFrameShape(QFrame.Shape.NoFrame)
        left_layout = QVBoxLayout(left_frame)
        left_layout.setContentsMargins(2, 2, 2, 2)
        left_layout.setSpacing(0)

        # Search bar
        self._search_bar = QLineEdit()
        self._search_bar.setPlaceholderText("Search mods...")
        self._search_bar.setClearButtonEnabled(True)
        self._search_bar.setFixedHeight(26)
        self._search_bar.textChanged.connect(self._on_search_changed)
        left_layout.addWidget(self._search_bar)
        left_layout.addSpacing(2)

        self._list_stack = QStackedWidget()

        self.mods_list = QListWidget()
        self.mods_list.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        self.mods_list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.mods_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.mods_list.customContextMenuRequested.connect(self._mod_context_menu)
        self.mods_list.currentRowChanged.connect(self._on_mod_selected)
        self.mods_list.itemDoubleClicked.connect(self._on_mod_double_clicked)

        self.patches_list = QListWidget()
        self.addons_list = QListWidget()
        self.exes_list = QListWidget()

        for lst in (self.patches_list, self.addons_list, self.exes_list):
            lst.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
            lst.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            lst.customContextMenuRequested.connect(
                lambda pos, lw=lst: self._addon_context_menu(lw, pos)
            )

        self._list_stack.addWidget(self.mods_list)     # index 0
        self._list_stack.addWidget(self.patches_list)  # index 1
        self._list_stack.addWidget(self.addons_list)   # index 2
        self._list_stack.addWidget(self.exes_list)     # index 3

        left_layout.addWidget(self._list_stack)

        content_row.addWidget(left_frame, 1)  # stretch=1, takes remaining space

        # == Right panel: QFrame with cyan border containing buttons ==
        right_frame = QFrame()
        right_frame.setObjectName("rightPanel")
        right_frame.setFrameShape(QFrame.Shape.NoFrame)
        right_frame.setFixedWidth(245)
        right_panel = QVBoxLayout(right_frame)
        right_panel.setContentsMargins(6, 6, 6, 6)
        right_panel.setSpacing(5)

        self.launch_btn = QPushButton("LAUNCH GAME")
        self.launch_btn.setFixedHeight(40)
        self.launch_btn.setFont(ui_font(13))
        self.launch_btn.setToolTip("Launch the game with selected mods through Steam/Proton")
        self.launch_btn.clicked.connect(self._on_launch)
        right_panel.addWidget(self.launch_btn)

        self.wb_btn = QPushButton("WORLD BUILDER")
        self.wb_btn.setFixedHeight(40)
        self.wb_btn.setFont(ui_font(13))
        self.wb_btn.setToolTip("Open Zero Hour World Builder map editor")
        self.wb_btn.clicked.connect(self._on_world_builder)
        right_panel.addWidget(self.wb_btn)

        self.windowed_btn = QPushButton("CHANGE TO FULL SCREEN")
        self.windowed_btn.setFixedHeight(40)
        self.windowed_btn.setFont(ui_font(13))
        self.windowed_btn.setToolTip("Toggle between windowed and fullscreen mode")
        self.windowed_btn.clicked.connect(self._toggle_windowed)
        self._update_windowed_btn()
        right_panel.addWidget(self.windowed_btn)

        self.quickstart_btn = QPushButton("CHANGE TO NORMAL START")
        self.quickstart_btn.setFixedHeight(40)
        self.quickstart_btn.setFont(ui_font(13))
        self.quickstart_btn.setToolTip("Toggle quickstart — skip intro videos and shell map")
        self.quickstart_btn.clicked.connect(self._toggle_quickstart)
        self._update_quickstart_btn()
        right_panel.addWidget(self.quickstart_btn)

        self.options_btn = QPushButton("OPTIONS")
        self.options_btn.setFixedHeight(40)
        self.options_btn.setFont(ui_font(13))
        self.options_btn.setToolTip("Graphics, camera, resolution, and launcher settings")
        self.options_btn.clicked.connect(self._open_options)
        right_panel.addWidget(self.options_btn)

        self.exit_btn = QPushButton("EXIT")
        self.exit_btn.setFixedHeight(40)
        self.exit_btn.setFont(ui_font(13))
        self.exit_btn.setToolTip("Close the launcher")
        self.exit_btn.clicked.connect(self.close)
        right_panel.addWidget(self.exit_btn)

        # ---- Navigation group: tabs + actions ----
        right_panel.addSpacing(12)

        self.mods_btn = QPushButton("Mods")
        self.mods_btn.setFixedHeight(25)
        self.mods_btn.setFont(ui_font(10))
        self.mods_btn.setToolTip("Show available mods")
        self.mods_btn.clicked.connect(self._show_mods_tab)
        right_panel.addWidget(self.mods_btn)

        self.patches_btn = QPushButton("Patches")
        self.patches_btn.setFixedHeight(25)
        self.patches_btn.setFont(ui_font(10))
        self.patches_btn.setToolTip("Show available patches")
        self.patches_btn.clicked.connect(self._show_patches_tab)
        right_panel.addWidget(self.patches_btn)

        self.addons_btn = QPushButton("Addons")
        self.addons_btn.setFixedHeight(25)
        self.addons_btn.setFont(ui_font(10))
        self.addons_btn.setToolTip("Show available addons")
        self.addons_btn.clicked.connect(self._show_addons_tab)
        right_panel.addWidget(self.addons_btn)

        right_panel.addSpacing(6)

        self.add_mod_btn = QPushButton("ADD MOD!")
        self.add_mod_btn.setFixedHeight(28)
        self.add_mod_btn.setFont(ui_font(12, bold=True))
        self.add_mod_btn.setToolTip("Browse and add mods from the online catalog")
        self.add_mod_btn.clicked.connect(self._on_add_mod)
        right_panel.addWidget(self.add_mod_btn)

        self.exes_btn = QPushButton("Executable files")
        self.exes_btn.setFixedHeight(28)
        self.exes_btn.setFont(ui_font(10))
        self.exes_btn.setToolTip("Manage game executables")
        self.exes_btn.clicked.connect(self._show_exes_tab)
        self.exes_btn.hide()  # Hidden: exe management via Options + PATCH GAME

        self.add_from_files_btn = QPushButton("Add mod from files")
        self.add_from_files_btn.setFixedHeight(28)
        self.add_from_files_btn.setFont(ui_font(11))
        self.add_from_files_btn.setToolTip("Install a mod from local archive files")
        self.add_from_files_btn.clicked.connect(self._on_add_from_files)
        right_panel.addWidget(self.add_from_files_btn)

        right_panel.addSpacing(6)

        # Community patch status label
        tag = self._settings.community_patch_tag
        short_tag = tag.replace("weekly-", "") if tag else ""
        if short_tag:
            status_text = f"Game patched ({short_tag})"
        else:
            status_text = "Game not patched"
        self._patch_status_label = QLabel(status_text)
        self._patch_status_label.setFont(ui_font(9))
        self._patch_status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._patch_status_label.setWordWrap(True)
        right_panel.addWidget(self._patch_status_label)

        # Patch progress bar (shown during community patch download)
        self._patch_progress = QProgressBar()
        self._patch_progress.setFixedHeight(20)
        self._patch_progress.setRange(0, 0)
        self._patch_progress.setTextVisible(True)
        self._patch_progress.setFormat("Patching...")
        self._patch_progress.hide()
        right_panel.addWidget(self._patch_progress)

        self.patch_game_btn = QPushButton("PATCH GAME")
        self.patch_game_btn.setFixedHeight(40)
        self.patch_game_btn.setFont(ui_font(13))
        self.patch_game_btn.setToolTip("Download latest TheSuperHackers community-patched game executable")
        self.patch_game_btn.clicked.connect(self._on_patch_game)
        right_panel.addWidget(self.patch_game_btn)

        patch_desc = QLabel("Download latest TheSuperHackers\ncommunity patch")
        patch_desc.setFont(ui_font(8))
        patch_desc.setAlignment(Qt.AlignmentFlag.AlignCenter)
        patch_desc.setStyleSheet(f"color: {DEFAULT_COLORS.inactive_border};")
        right_panel.addWidget(patch_desc)

        self._latest_patch_tag = ""  # filled by background check

        right_panel.addSpacing(8)

        # Update launcher button — hidden until background check finds a new version
        self.update_launcher_btn = QPushButton("UPDATE LAUNCHER")
        self.update_launcher_btn.setFixedHeight(32)
        self.update_launcher_btn.setFont(ui_font(10))
        self.update_launcher_btn.setToolTip("Download and install the latest GenLauncher-LP version")
        self.update_launcher_btn.clicked.connect(self._on_update_launcher)
        self.update_launcher_btn.hide()
        self._latest_launcher_tag = ""
        right_panel.addWidget(self.update_launcher_btn)

        # Push remaining space so buttons stay at top, about at bottom
        right_panel.addStretch(1)

        # Game selector dropdown
        game_label = QLabel("Game:")
        game_label.setFont(ui_font(9))
        game_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        right_panel.addWidget(game_label)

        self._game_combo = QComboBox()
        self._game_combo.setFixedHeight(28)
        self._game_combo.setFont(ui_font(10))
        self._populate_game_combo()
        self._game_combo.currentIndexChanged.connect(self._on_game_changed)
        right_panel.addWidget(self._game_combo)

        right_panel.addSpacing(4)

        # Report a bug button
        self.report_bug_btn = QPushButton("Report a Bug")
        self.report_bug_btn.setFixedHeight(24)
        self.report_bug_btn.setFont(ui_font(9))
        self.report_bug_btn.setToolTip("Open GitHub Issues page to report a bug")
        self.report_bug_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.report_bug_btn.clicked.connect(
            lambda: webbrowser.open("https://github.com/NeleBiH/GenLauncher-LP/issues")
        )
        right_panel.addWidget(self.report_bug_btn)

        right_panel.addSpacing(2)

        # About button
        self.about_btn = QPushButton("About")
        self.about_btn.setFixedHeight(28)
        self.about_btn.setFont(ui_font(10))
        self.about_btn.setToolTip("Credits, links, and feature overview")
        self.about_btn.clicked.connect(self._on_about)
        right_panel.addWidget(self.about_btn)

        # Version label — click to copy
        from importlib.metadata import version as pkg_version
        try:
            _ver = pkg_version("genlauncher-linux")
        except Exception:
            _ver = "?"
        self._version_label = QLabel(f"v{_ver}")
        self._version_label.setFont(ui_font(9))
        self._version_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._version_label.setStyleSheet(
            f"color: {DEFAULT_COLORS.inactive_border}; padding: 2px 0;"
        )
        self._version_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self._version_label.setToolTip("Click to copy version to clipboard")
        self._version_label.mousePressEvent = lambda _: self._copy_version(_ver)
        right_panel.addWidget(self._version_label)

        # Launcher update button + progress (inside right panel, hidden by default)
        self.launcher_update_btn = QPushButton("Update!")
        self.launcher_update_btn.setFixedHeight(35)
        self.launcher_update_btn.setFont(ui_font(13))
        self.launcher_update_btn.hide()
        right_panel.addWidget(self.launcher_update_btn)

        progress_container = QWidget()
        progress_container.setFixedHeight(24)
        progress_layout = QVBoxLayout(progress_container)
        progress_layout.setContentsMargins(0, 0, 0, 0)

        self.launcher_progress = QProgressBar()
        self.launcher_progress.setFixedHeight(24)
        self.launcher_progress.hide()
        progress_layout.addWidget(self.launcher_progress)

        self.launcher_progress_text = QLabel()
        self.launcher_progress_text.setFont(ui_font(9, bold=True))
        self.launcher_progress_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.launcher_progress_text.setStyleSheet(
            f"color: {DEFAULT_COLORS.download_text};"
        )
        self.launcher_progress_text.hide()
        right_panel.addWidget(progress_container)
        self.launcher_progress_text.setParent(progress_container)
        self.launcher_progress_text.setGeometry(2, 2, 225, 20)

        content_row.addWidget(right_frame)
        main_layout.addLayout(content_row, 1)  # stretch=1 for main content

        # ---- Bottom bar: version labels (bottom-left corner) ----
        bottom_bar = QHBoxLayout()
        bottom_bar.setContentsMargins(0, 2, 0, 0)

        from importlib.metadata import version as pkg_version
        try:
            ver = pkg_version("genlauncher-linux")
        except Exception:
            ver = "0.1.0-alpha"

        self.version_label = QLabel(f"GenLauncher-LP v{ver}")
        self.version_label.setFixedHeight(20)
        self.version_label.setFont(ui_font(10))
        bottom_bar.addWidget(self.version_label)

        bottom_bar.addStretch(1)  # push label to the left

        # Loading progress bar (replaces version label while catalog loads)
        self._loading_bar = QProgressBar()
        self._loading_bar.setFixedHeight(20)
        self._loading_bar.setRange(0, 0)  # indeterminate
        self._loading_bar.setTextVisible(True)
        self._loading_bar.setFormat("Loading catalog...")
        bottom_bar.addWidget(self._loading_bar)
        self._loading_bar.hide()

        # Game patch version (bottom-right corner)
        tag = self._settings.community_patch_tag
        patch_ver = tag.replace("weekly-", "") if tag else "not installed"
        self._patch_version_label = QLabel(f"Game patch: {patch_ver}")
        self._patch_version_label.setFixedHeight(20)
        self._patch_version_label.setFont(ui_font(10))
        bottom_bar.addWidget(self._patch_version_label)

        main_layout.addLayout(bottom_bar)

    # ---- tab switching ------------------------------------------------------

    def _show_mods_tab(self):
        self._list_stack.setCurrentIndex(0)
        self.add_mod_btn.show()
        self.add_from_files_btn.setText("Add mod from files")
        self.exes_btn.show()

    def _show_patches_tab(self):
        self._list_stack.setCurrentIndex(1)
        self.add_mod_btn.hide()
        self.exes_btn.hide()
        name = self._selected_mod.mod_name if self._selected_mod else ""
        self.add_from_files_btn.setText(f"Add patch for {name} from files" if name else "Add patch from files")
        self._refresh_patches_list()

    def _show_addons_tab(self):
        self._list_stack.setCurrentIndex(2)
        self.add_mod_btn.hide()
        self.exes_btn.hide()
        name = self._selected_mod.mod_name if self._selected_mod else ""
        self.add_from_files_btn.setText(f"Add addon for {name} from files" if name else "Add addon from files")
        self._refresh_addons_list()

    def _show_exes_tab(self):
        self._list_stack.setCurrentIndex(3)
        self.add_mod_btn.hide()
        self.exes_btn.hide()
        name = self._selected_mod.mod_name if self._selected_mod else ""
        self.add_from_files_btn.setText(
            f"Add exe for {name} from files" if name else "Add exe from files"
        )
        self._refresh_exes_list()

    # ---- mod list population ------------------------------------------------

    def _on_mod_rating_changed(self, mod_name: str, rating: int):
        """Save mod rating and re-sort the list."""
        self._settings.mod_ratings[mod_name] = rating
        self._settings.save()
        self._refresh_mod_list()

    def _refresh_mod_list(self):
        self.mods_list.clear()
        if not self._catalog:
            return

        restore_row = -1
        last_mod = self._settings.last_selected_mod

        # Sort: higher-rated mods first, then alphabetical
        ratings = self._settings.mod_ratings
        entries = sorted(
            self._catalog.mod_datas,
            key=lambda e: (-ratings.get(e.mod_name, 0), e.mod_name.casefold()),
        )

        for entry in entries:
            mv = self._catalog_mods.get(entry.mod_name.casefold())
            if mv is None:
                mv = ModificationVersion(
                    name=entry.mod_name,
                    mod_type=ModType.MOD,
                )

            widget = ModItemWidget(mv)
            widget.update_clicked.connect(self._on_update_mod)
            widget.changelog_clicked.connect(self._on_changelog)
            widget.support_clicked.connect(self._on_support)
            widget.version_changed.connect(self._on_version_changed)
            widget.rating_changed.connect(self._on_mod_rating_changed)
            widget.star_rating.set_rating(ratings.get(mv.name, 0))

            # Check if installed / has update
            installed = self._store.get(mv.name)
            is_installed = installed is not None and installed.latest is not None
            has_update = is_installed and installed.has_update(mv)

            widget.set_update_state(is_installed, has_update)

            if is_installed:
                widget.version_label.setText(installed.latest.version)
                versions = [v.version for v in installed.sorted_versions()]
                widget.set_versions(versions)
                if has_update:
                    widget.start_blink()

            item = QListWidgetItem(self.mods_list)
            item.setSizeHint(QSize(ModItemWidget.WIDGET_WIDTH, ModItemWidget.WIDGET_HEIGHT))
            item.setData(Qt.ItemDataRole.UserRole, entry)
            self.mods_list.setItemWidget(item, widget)

            if last_mod and entry.mod_name == last_mod:
                restore_row = self.mods_list.count() - 1

        # Restore last selected mod
        if restore_row >= 0:
            self.mods_list.setCurrentRow(restore_row)

        # Apply current search filter
        self._apply_search_filter()

        # Start async image loading
        self._start_image_loading()

    def _on_search_changed(self, text: str):
        """Filter visible mods by search text."""
        self._apply_search_filter()

    def _apply_search_filter(self):
        """Show/hide mod list items based on search bar text."""
        query = self._search_bar.text().strip().casefold()
        for i in range(self.mods_list.count()):
            item = self.mods_list.item(i)
            entry = item.data(Qt.ItemDataRole.UserRole)
            if not query or query in entry.mod_name.casefold():
                item.setHidden(False)
            else:
                item.setHidden(True)

    def _start_image_loading(self):
        """Download mod preview images in background."""
        requests: list[tuple[str, str]] = []
        for entry in (self._catalog.mod_datas if self._catalog else []):
            mv = self._catalog_mods.get(entry.mod_name.casefold())
            if mv and mv.ui_image_source_link:
                requests.append((mv.name, mv.ui_image_source_link))

        if not requests:
            return

        if self._image_loader and self._image_loader.isRunning():
            return  # Already loading

        self._image_loader = ImageLoader(
            requests, self._paths.cache_dir, parent=self
        )
        self._image_loader.image_ready.connect(self._on_image_ready)
        self._image_loader.image_missing.connect(self._on_image_missing)
        self._image_loader.start()

    def _on_image_missing(self, mod_name: str):
        """No usable banner for this mod (dead link, rate limit, no entry)."""
        for i in range(self.mods_list.count()):
            widget = self.mods_list.itemWidget(self.mods_list.item(i))
            if widget and hasattr(widget, "mv") and widget.mv.name == mod_name:
                widget.set_placeholder()
                break

    def _on_image_ready(self, mod_name: str, data: bytes):
        """Set downloaded image on the corresponding mod widget."""
        pix = QPixmap()
        pix.loadFromData(data)
        if pix.isNull():
            self._on_image_missing(mod_name)
            return
        # Scale to 500x100
        pix = pix.scaled(
            500, 100,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        # Find widget by mod name
        for i in range(self.mods_list.count()):
            widget = self.mods_list.itemWidget(self.mods_list.item(i))
            if widget and hasattr(widget, "mv") and widget.mv.name == mod_name:
                widget.set_image(pix)
                break

    #: Catalog entries that apply to the unmodded game rather than to any mod.
    BASE_GAME_ALIASES = frozenset({"original game", ""})

    def _refresh_patches_list(self):
        self.patches_list.clear()
        if not self._selected_mod:
            return
        mod_name = self._selected_mod.mod_name.casefold()
        for mv in self._catalog_patches:
            dep = mv.dependence_name.casefold()
            if dep != mod_name:
                continue
            self._add_addon_item(self.patches_list, mv)
        # Patches and addons for the vanilla game are useful regardless of which
        # mod is selected, and the catalog files them under "Original Game".
        # Without this they could never be reached from the GUI.
        for mv in self._catalog_patches:
            if mv.dependence_name.casefold() in self.BASE_GAME_ALIASES:
                self._add_addon_item(self.patches_list, mv)

    def _refresh_addons_list(self):
        self.addons_list.clear()
        if not self._selected_mod:
            return
        mod_name = self._selected_mod.mod_name.casefold()

        # Collect patch names for this mod (addons can depend on patches too)
        patch_names = set()
        for mv in self._catalog_patches:
            if mv.dependence_name.casefold() in (mod_name, *self.BASE_GAME_ALIASES):
                patch_names.add(mv.name.casefold())

        for mv in self._catalog_addons:
            dep = mv.dependence_name.casefold()
            base = dep in self.BASE_GAME_ALIASES
            # Show addon if it depends on this mod, on one of its patches,
            # or on the vanilla game.
            if dep != mod_name and dep not in patch_names and not base:
                continue
            self._add_addon_item(self.addons_list, mv)

    def _refresh_exes_list(self):
        self.exes_list.clear()
        for mv in self._catalog_exes:
            self._add_addon_item(self.exes_list, mv)

    def _catalog_version_for(self, mv: ModificationVersion) -> ModificationVersion | None:
        """Find this entry in the catalog, whichever list it lives in.

        The update check used to look only in _catalog_mods, so a selected
        patch, addon or executable was never reported as out of date.
        """
        key = mv.name.casefold()
        found = self._catalog_mods.get(key)
        if found is not None:
            return found
        for group in (
            getattr(self, "_catalog_patches", []),
            getattr(self, "_catalog_addons", []),
            getattr(self, "_catalog_exes", []),
        ):
            for candidate in group:
                if candidate.name.casefold() == key:
                    return candidate
        return None

    def _add_addon_item(self, list_widget: QListWidget, mv: ModificationVersion):
        widget = AddonItemWidget(mv)
        widget.update_clicked.connect(self._on_update_mod)
        widget.changelog_clicked.connect(self._on_changelog)
        widget.rating_changed.connect(self._on_mod_rating_changed)
        widget.star_rating.set_rating(self._settings.mod_ratings.get(mv.name, 0))

        # Without this every patch/addon/exe row kept its constructor default
        # ("Update!", enabled) regardless of what is actually installed, and an
        # available update never blinked.
        installed = self._store.get(mv.name)
        is_installed = installed is not None and installed.latest is not None
        has_update = is_installed and installed.has_update(mv)
        widget.set_update_state(is_installed, has_update)
        if is_installed:
            widget.version_label.setText(installed.latest.version)
            if has_update:
                widget.start_blink()

        item = QListWidgetItem(list_widget)
        item.setSizeHint(QSize(AddonItemWidget.WIDGET_WIDTH, AddonItemWidget.WIDGET_HEIGHT))
        item.setData(Qt.ItemDataRole.UserRole, mv)
        list_widget.setItemWidget(item, widget)

    # ---- catalog data injection (called from app.py) -------------------------

    def set_catalog_data(
        self,
        catalog: Catalog,
        mods: dict[str, ModificationVersion],
        patches: list[ModificationVersion],
        addons: list[ModificationVersion],
        exes: list[ModificationVersion],
    ):
        self._catalog = catalog
        self._catalog_mods = mods
        self._catalog_patches = patches
        self._catalog_addons = addons
        self._catalog_exes = exes

        self._refresh_mod_list()

    # ---- mod selection ------------------------------------------------------

    def _on_mod_selected(self, row: int):
        if row < 0:
            self._selected_mod = None
            self._apply_theme(DEFAULT_COLORS)
            return
        item = self.mods_list.item(row)
        if item:
            entry = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(entry, RawModEntry):
                self._selected_mod = entry
                # Update tab labels
                name = entry.mod_name
                self.patches_btn.setText(f"Patches for {name}")
                self.addons_btn.setText(f"Addons for {name}")
                # Save last selected mod
                self._settings.last_selected_mod = name
                self._settings.save()

                # Apply per-mod theme colors and background
                mv = self._catalog_mods.get(entry.mod_name.casefold())
                if mv and mv.colors and not mv.colors.is_empty():
                    self._apply_theme(colors_from_mod(mv.colors))
                    if mv.colors.background_image_link:
                        self._load_mod_background(mv.name, mv.colors.background_image_link)
                    else:
                        self._reset_background()
                else:
                    self._apply_theme(DEFAULT_COLORS)
                    self._reset_background()

    def _on_mod_double_clicked(self, item):
        """Show mod info dialog on double-click."""
        entry = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(entry, RawModEntry):
            return
        mv = self._catalog_mods.get(entry.mod_name.casefold())
        if mv is None:
            return
        # Check install status
        inst = self._store.get(entry.mod_name)
        if inst and inst.latest:
            mv_copy = inst.latest
        else:
            mv_copy = mv
        from .mod_info_dialog import ModInfoDialog
        dlg = ModInfoDialog(mv_copy, parent=self)
        dlg.exec()

    def _on_version_changed(self, mv: ModificationVersion, version: str):
        """Track user's version selection for launch."""
        self._selected_versions[mv.name.casefold()] = version

    # ---- theme switching ----------------------------------------------------

    def _apply_theme(self, colors: Colors):
        """Apply a color theme to the entire window."""
        if colors == self._current_colors:
            return
        self._current_colors = colors
        app = QApplication.instance()
        if app:
            app.setStyleSheet(generate_qss(colors))
        # Update hardcoded color references
        self.launcher_progress_text.setStyleSheet(
            f"color: {colors.download_text};"
        )

    def _reset_background(self):
        """Restore the default background image."""
        game = self._settings.game
        bg_path = BACKGROUND_GENERALS if game == "generals" else BACKGROUND_ZH
        self._bg_pixmap = QPixmap(bg_path)
        self.update()

    def _load_mod_background(self, mod_name: str, url: str):
        """Load a custom mod background from cache or download it."""
        safe = mod_name.replace("/", "_").replace("\\", "_")
        cache_file = self._paths.cache_dir / "images" / f"{safe}_bg.png"

        if cache_file.exists():
            pix = QPixmap(str(cache_file))
            if not pix.isNull():
                self._bg_pixmap = pix
                self.update()
                return

        # Download in background thread (reuse ImageLoader pattern)
        import threading

        def _download():
            try:
                import httpx
                with httpx.Client(timeout=15, follow_redirects=True) as client:
                    resp = client.get(url)
                    resp.raise_for_status()
                    cache_file.parent.mkdir(parents=True, exist_ok=True)
                    cache_file.write_bytes(resp.content)
                # Schedule UI update on main thread (thread-safe)
                QMetaObject.invokeMethod(
                    self, "_set_mod_background_str",
                    Qt.ConnectionType.QueuedConnection,
                    Q_ARG(str, str(cache_file)),
                )
            except Exception as exc:
                log.debug("mod background download failed for %s: %s", mod_name, exc)

        threading.Thread(target=_download, daemon=True).start()

    @Slot(str)
    def _set_mod_background_str(self, path_str: str):
        """Thread-safe slot wrapper for _set_mod_background."""
        self._set_mod_background(Path(path_str))

    def _set_mod_background(self, path: Path):
        """Set background from a cached file (called on main thread)."""
        pix = QPixmap()
        if not os.environ.get("GENLAUNCHER_NO_BG"):
            pix = QPixmap(str(path))
        if not pix.isNull():
            self._bg_pixmap = pix
            self.update()

    # ---- downloads ----------------------------------------------------------

    def _on_update_mod(self, mv: ModificationVersion):
        key = mv.name.casefold()
        if key in self._active_downloads:
            # Cancel active download
            self._active_downloads[key].cancel()
            return

        if not mv.simple_download_link and not mv.uses_s3:
            InfoDialog.warning(
                self, "No download",
                f"No download link available for {mv.name}.\n"
                "The mod's hosting may be offline.",
            )
            return

        # Confirm before downloading
        if not InfoDialog.question(
            self,
            "Download",
            f"Do you want to download {mv.name} {mv.version}?",
        ):
            return

        # Choose S3 or HTTP worker based on mod metadata.
        if mv.uses_s3:
            # Find previously installed version dir for file reuse.
            prev_dir = self._find_prev_version_dir(mv)
            worker = S3DownloadInstallWorker(
                mv, self._paths, prev_version_dir=prev_dir, parent=self,
            )
            worker.progress.connect(lambda p: self._on_s3_download_progress(mv, p))
        else:
            worker = DownloadInstallWorker(mv, self._paths, parent=self)
            worker.progress.connect(lambda p: self._on_download_progress(mv, p))

        self._active_downloads[key] = worker

        # Find the widget for this mod to update its progress
        widget = self._find_mod_widget(mv)
        if widget:
            widget.set_download_mode(True)

        worker.status.connect(lambda s: self._on_download_status(mv, s))
        worker.finished.connect(lambda m: self._on_download_finished(m))
        worker.cancelled.connect(lambda: self._on_download_cancelled(mv))
        worker.error.connect(lambda e: self._on_download_error(mv, e))

        self._show_download_spinner(True)
        worker.start()

    def _find_prev_version_dir(self, mv: ModificationVersion) -> Path | None:
        """Find the most recent installed version folder for file reuse in S3."""
        installed = self._store.get(mv.name)
        if not installed:
            return None
        for ver_str, ver_mv in sorted(
            installed.versions.items(), key=lambda kv: kv[1].sort_key, reverse=True,
        ):
            if ver_str == mv.version:
                continue
            folder = ver_mv.folder_name(self._paths.mods_dir)
            if folder.is_dir():
                return folder
        return None

    def _on_s3_download_progress(self, mv: ModificationVersion, p):
        """Handle S3 multi-file progress (S3Progress dataclass)."""
        widget = self._find_mod_widget(mv)
        if widget:
            pct = int(p.fraction * 100)
            speed = p.speed_str
            dl_mb = p.downloaded / 1048576
            total_mb = p.total / 1048576
            text = f"{dl_mb:.1f} / {total_mb:.1f} MB ({p.files_done}/{p.files_total} files)"
            if speed:
                text += f"  {speed}"
            widget.show_progress(pct, text)

    def _on_download_progress(self, mv: ModificationVersion, p: Progress):
        widget = self._find_mod_widget(mv)
        if widget:
            pct = int(p.fraction * 100)
            speed = p.speed_str
            text = f"{p.megabytes} / {p.total_megabytes} MB"
            if speed:
                text += f"  ({speed})"
            widget.show_progress(pct, text)

    def _on_download_status(self, mv: ModificationVersion, status: str):
        widget = self._find_mod_widget(mv)
        if widget:
            widget.show_progress(widget.progress_bar.value(), status)

    def _on_download_finished(self, mv: ModificationVersion):
        key = mv.name.casefold()
        self._active_downloads.pop(key, None)
        widget = self._find_mod_widget(mv)
        if widget:
            widget.set_download_mode(False)
            widget.version_label.setText(mv.version)
            widget.stop_blink()
            widget.set_update_state(installed=True, has_update=False)

        if not self._active_downloads:
            self._show_download_spinner(False)

        # Auto-delete old versions if enabled
        if self._settings.auto_delete_old_versions:
            self._delete_old_versions(mv)

        self._store = ModStore(self._paths.data_dir)  # reload

    def _delete_old_versions(self, mv: ModificationVersion):
        """Remove older installed versions of a mod, keeping only the latest."""
        import shutil

        installed = self._store.get(mv.name)
        if not installed:
            return
        for ver_str, ver_mv in list(installed.versions.items()):
            if ver_str == mv.version:
                continue  # keep the just-installed version
            folder = ver_mv.folder_name(self._paths.mods_dir)
            if folder.is_dir():
                try:
                    shutil.rmtree(folder)
                    log.info("Auto-deleted old version: %s %s", mv.name, ver_str)
                except OSError as exc:
                    log.warning("Failed to delete old version %s %s: %s", mv.name, ver_str, exc)

    def _on_download_cancelled(self, mv: ModificationVersion):
        """Release the slot the cancelled download was holding.

        Without this the entry stayed in _active_downloads forever, which made
        _on_launch refuse to start the game and left the spinner on screen.
        """
        key = mv.name.casefold()
        self._active_downloads.pop(key, None)
        widget = self._find_mod_widget(mv)
        if widget:
            widget.set_download_mode(False)
            widget.hide_progress()
            widget.set_update_state(
                installed=bool(getattr(widget, "mv", None) and widget.mv.installed),
                has_update=False,
            )
            widget.stop_blink()

        if not self._active_downloads:
            self._show_download_spinner(False)

    def _on_download_error(self, mv: ModificationVersion, error: str):
        key = mv.name.casefold()
        self._active_downloads.pop(key, None)
        widget = self._find_mod_widget(mv)
        if widget:
            widget.set_download_mode(False)
            widget.show_progress(0, f"Error: {error[:60]}")

        if not self._active_downloads:
            self._show_download_spinner(False)

        InfoDialog.critical(self, "Download Error", f"{mv.name}: {error}")

    def _show_download_spinner(self, show: bool):
        pass  # spinner removed; each mod widget has its own progress bar

    def _find_mod_widget(self, mv: ModificationVersion) -> ModItemWidget | AddonItemWidget | None:
        """Find the list item widget for a given ModificationVersion."""
        for list_w in (self.mods_list, self.patches_list, self.addons_list, self.exes_list):
            for i in range(list_w.count()):
                widget = list_w.itemWidget(list_w.item(i))
                if widget and hasattr(widget, "mv") and widget.mv.name.casefold() == mv.name.casefold():
                    return widget
        return None

    # ---- game launch --------------------------------------------------------

    def _on_launch(self):
        if self._is_game_running:
            InfoDialog.information(self, "Game Running", "Game is still running!")
            return

        # Check if the game is already running outside of our control
        from ..core import process as process_watch
        from ..core.config import GAME_EXE_CANDIDATES
        watch_names = process_watch.normalize_names(GAME_EXE_CANDIDATES)
        if process_watch.pids_matching(watch_names):
            InfoDialog.warning(
                self, "Game Already Running",
                "The game appears to be running already.\n"
                "Close it before launching again.",
            )
            return

        if self._active_downloads:
            InfoDialog.warning(
                self, "Downloads Active",
                "Please finish all downloads before launching.",
            )
            return

        # Collect selected mods
        from ..core import steam as steam_mod

        try:
            install = self._resolve_install()
        except FileNotFoundError as exc:
            InfoDialog.critical(self, "Error", str(exc))
            return

        selected = self._get_selected_versions()

        # Pre-launch validation
        if selected:
            # Check 1: Are all selected mods installed?
            not_installed = [v for v in selected if not v.installed]
            if not_installed:
                names = ", ".join(v.name for v in not_installed)
                InfoDialog.warning(
                    self, "Mods Not Installed",
                    f"The following mods are not installed:\n{names}\n\n"
                    "Install them before launching.",
                )
                return

            # Check 2: File integrity (if enabled)
            if self._settings.check_mod_files:
                do_check = True
                if self._settings.ask_before_check:
                    do_check = InfoDialog.question(
                        self, "File Check",
                        "Check mod file integrity before launching?",
                    )

                if do_check:
                    self._run_integrity_check_then_continue(selected, install)
                    return

            # Check 3: Any updates available?
            has_updates = []
            for v in selected:
                cat_mv = self._catalog_version_for(v)
                if cat_mv:
                    inst = self._store.get(v.name)
                    if inst and inst.has_update(cat_mv):
                        has_updates.append(v.name)
            if has_updates:
                names = ", ".join(has_updates)
                if not InfoDialog.question(
                    self, "Updates Available",
                    f"Updates available for: {names}\n\n"
                    "Launch anyway?",
                ):
                    return

        self._link_and_launch(selected, install)

    def _run_integrity_check_then_continue(self, selected, install):
        """Hash the selected mods on a worker, then carry on or stop.

        This used to run verify_checksums() inline, which froze the window for
        as long as it took to hash multiple gigabytes.
        """
        folders = [v.folder_name(self._paths.mods_dir) for v in selected]
        self._disable_ui()
        worker = IntegrityWorker(folders, parent=self)
        self._integrity_worker = worker

        def on_checked(result) -> None:
            self._integrity_worker = None
            self._enable_ui()
            if not result.ok:
                log.warning("integrity: %s", result.summary)
                for kind, paths in (
                    ("missing", result.missing),
                    ("corrupted", result.corrupted),
                    ("extra", result.extra),
                ):
                    for path in paths:
                        log.warning("integrity %s: %s", kind, path)
                review = IntegrityReviewDialog(result, parent=self)
                if review.exec() != QDialog.DialogCode.Accepted:
                    log.info("launch cancelled after integrity review")
                    return
            elif result.no_baseline:
                log.info("Integrity: no baseline recorded, nothing verified")
            self._link_and_launch(selected, install)

        worker.checked.connect(on_checked)
        worker.start()

    def _link_and_launch(self, selected, install):
        """Patch, link, and start the game."""
        log.info(
            "launch selection: %s",
            ", ".join(f"{v.name} {v.version}" for v in selected) or "(none)",
        )
        if not selected:
            # Not an error - vanilla is a legitimate choice - but it used to be
            # invisible, so a mis-click looked exactly like "mod never loaded".
            log.warning("launching vanilla: nothing is selected")

        # Apply camera height patch before linking
        from ..core.big import find_big_with_gamedata, set_camera_height

        patched_big: Path | None = None
        if self._settings.camera_height > 0:
            if selected:
                # Patch the mod's .big/.gib first; fall back to base game .big
                mod_folders = [v.folder_name(self._paths.mods_dir) for v in selected]
                big_file = find_big_with_gamedata(
                    mod_folders, fallback_folders=[install.game_dir],
                )
            else:
                # Vanilla launch: patch the game's own .big file
                big_file = find_big_with_gamedata([install.game_dir])
            if big_file:
                if set_camera_height(big_file, self._settings.camera_height):
                    patched_big = big_file
                    self._pending_camera_big = patched_big

        # Link
        self._disable_ui()
        linker_strategy = LinkStrategy.parse(self._settings.link_strategy)

        link_worker = LinkWorker(
            install.game_dir,
            self._paths.journal_file,
            self._paths.mods_dir,
            selected,
            strategy=linker_strategy,
            parent=self,
        )

        def on_linked(report):
            if not report.ok:
                for err in report.errors:
                    log.error("link error: %s", err)
                InfoDialog.warning(
                    self, "Link Errors",
                    "Some files could not be linked:\n" + "\n".join(report.errors[:5]),
                )

            # Now launch the game. install.appid/compat_data come from Steam
            # discovery - Settings.appid is only a user override.
            runner = GameRunner(
                install.game_dir,
                self._settings,
                appid=install.appid,
                compat_data=install.compat_data,
                exe=self._chosen_game_exe(install.game_dir),
                mode=install.mode,
                parent=self,
            )
            self._game_runner = runner
            self._is_game_running = True
            # GameRunner.output was never connected, so every line the game or
            # Proton printed was discarded exactly when support needed it.
            runner.output.connect(self._on_process_output)

            runner.finished.connect(
                lambda rc, dur: self._on_game_exited(install, linker_strategy, rc, patched_big, dur)
            )
            runner.error.connect(
                lambda e: self._on_game_error(install, linker_strategy, e, patched_big)
            )
            runner.started_ok.connect(lambda: self._on_game_started())

            if self._settings.hide_launcher:
                self.hide()

            runner.start()

        link_worker.finished.connect(on_linked)
        link_worker.error.connect(lambda e: self._on_link_error(e))
        link_worker.start()

    def _on_world_builder(self):
        """Launch World Builder, with the selected mods linked in.

        World Builder is a Windows exe living in the same prefix as the game, so
        it has to go through Proton rather than being exec'd directly, and it
        needs the selected mods linked exactly like the game does -- otherwise
        it opens with the stock rulesets instead of the ones you picked.
        """
        from ..core import steam as steam_mod

        if self._is_game_running:
            InfoDialog.information(self, "Game Running", "The game is still running!")
            return

        try:
            install = self._resolve_install()
        except FileNotFoundError as exc:
            InfoDialog.critical(self, "Error", str(exc))
            return

        wb = self._chosen_executable(
            install.game_dir,
            self._settings.world_builder_exe,
            steam_mod.resolve_world_builder_exe(install.game_dir),
        )
        if wb is None:
            InfoDialog.warning(
                self, "Not Found",
                "WorldBuilder.exe not found in game directory.\n"
                "It may not be installed with this version of the game.",
            )
            return

        selected = self._get_selected_versions()
        patched_big: Path | None = None
        strategy = LinkStrategy.parse(self._settings.link_strategy)

        def launch_wb():
            runner = GameRunner(
                install.game_dir,
                self._settings,
                appid=install.appid,
                compat_data=install.compat_data,
                exe=wb,
                args_override=[],
                parent=self,
            )
            self._game_runner = runner
            self._is_game_running = True
            runner.output.connect(self._on_process_output)
            runner.finished.connect(
                lambda rc, dur: self._on_game_exited(install, strategy, rc, patched_big, dur)
            )
            runner.error.connect(
                lambda e: self._on_game_error(install, strategy, e, patched_big)
            )
            runner.started_ok.connect(self._on_world_builder_started)
            runner.start()

        if selected:
            from ..core.big import find_big_with_gamedata, set_camera_height

            if self._settings.camera_height > 0:
                mod_folders = [v.folder_name(self._paths.mods_dir) for v in selected]
                big_file = find_big_with_gamedata(
                    mod_folders, fallback_folders=[install.game_dir],
                )
                if big_file and set_camera_height(big_file, self._settings.camera_height):
                    patched_big = big_file
                    self._pending_camera_big = patched_big

            self._disable_ui()
            link_worker = LinkWorker(
                install.game_dir,
                self._paths.journal_file,
                self._paths.mods_dir,
                selected,
                strategy=strategy,
                parent=self,
            )

            def on_linked(report):
                if not report.ok:
                    for err in report.errors:
                        log.error("world builder link error: %s", err)
                    self._restore_camera_patch(patched_big)
                    self._enable_ui()
                    InfoDialog.warning(
                        self, "Link Errors",
                        "Some files could not be linked:\n"
                        + "\n".join(report.errors[:5]),
                    )
                    return
                if self._settings.hide_launcher:
                    self.hide()
                launch_wb()

            link_worker.finished.connect(on_linked)
            link_worker.error.connect(lambda e: self._on_link_error(e))
            link_worker.start()
        else:
            launch_wb()

    def _on_world_builder_started(self):
        log.info("World Builder started")

    def _on_process_output(self, line: str):
        """Game/tool stdout, kept in the log for support requests."""
        if line.strip():
            log.info("proc: %s", line.rstrip())


    def _on_game_started(self):
        log.info("Game started")

    def _on_game_exited(self, install, strategy, returncode: int, patched_big=None, duration: float = 0.0):
        self._is_game_running = False
        self._game_runner = None
        self.show()

        # Crash detection: if the game ran for less than 12 seconds, it likely crashed
        if duration > 0 and duration < 12.0:
            log.warning("Game exited after %.1f seconds (possible crash), rc=%d", duration, returncode)
            InfoDialog.warning(
                self, "Possible Crash",
                f"The game exited after {duration:.0f} seconds.\n"
                "This usually indicates a crash.\n\n"
                "Check that your mods are installed correctly,\n"
                "or try launching without mods to verify.",
            )

        # Restore camera height backup
        self._restore_camera_patch(patched_big)

        # Unlink
        unlink_worker = UnlinkWorker(
            install.game_dir,
            self._paths.journal_file,
            self._paths.mods_dir,
            strategy=strategy,
            parent=self,
        )
        unlink_worker.finished.connect(lambda r: self._enable_ui())
        unlink_worker.error.connect(lambda e: self._on_link_error(e))
        unlink_worker.start()

    def _on_game_error(self, install, strategy, error: str, patched_big=None):
        self._is_game_running = False
        self._game_runner = None
        self.show()
        InfoDialog.critical(self, "Game Error", error)

        # Restore camera height backup
        self._restore_camera_patch(patched_big)

        # Try to unlink anyway
        unlink_worker = UnlinkWorker(
            install.game_dir,
            self._paths.journal_file,
            self._paths.mods_dir,
            strategy=strategy,
            parent=self,
        )
        unlink_worker.finished.connect(lambda r: self._enable_ui())
        # Without this a failed unlink leaves every button disabled forever.
        unlink_worker.error.connect(self._on_link_error)
        unlink_worker.start()

    def _restore_camera_patch(self, patched_big: Path | None = None) -> None:
        """Put a patched .big back from its .GOF backup, at most once.

        Without this the patched INI.big and its .GOF backup survived every
        failure that was not a clean game exit.
        """
        from ..core.big import restore_camera_height

        target = patched_big or self._pending_camera_big
        if target is None:
            return
        try:
            if restore_camera_height(target):
                log.info("Restored camera height patch in %s", target.name)
        except Exception as exc:  # pragma: no cover - restore is best effort
            log.error("Failed to restore camera height in %s: %s", target.name, exc)
        if self._pending_camera_big == target:
            self._pending_camera_big = None

    def _on_link_error(self, error: str):
        self._enable_ui()
        # Linking never completed, so the camera patch is ours to undo.
        self._restore_camera_patch()
        InfoDialog.critical(self, "Link Error", error)

    def _get_selected_versions(self) -> list[ModificationVersion]:
        """Collect ModificationVersions for all selected mods/patches/addons."""
        versions: list[ModificationVersion] = []

        # Selected mod (use user-chosen version if available)
        row = self.mods_list.currentRow()
        if row >= 0:
            item = self.mods_list.item(row)
            entry = item.data(Qt.ItemDataRole.UserRole) if item else None
            if isinstance(entry, RawModEntry):
                installed = self._store.get(entry.mod_name)
                if installed:
                    chosen = self._selected_versions.get(entry.mod_name.casefold())
                    if chosen:
                        ver = self._store.find_version(entry.mod_name, chosen)
                        if ver:
                            versions.append(ver)
                    elif installed.latest:
                        versions.append(installed.latest)

        # Selected patches (all selected items)
        for i in range(self.patches_list.count()):
            item = self.patches_list.item(i)
            if item and item.isSelected():
                mv = item.data(Qt.ItemDataRole.UserRole)
                if isinstance(mv, ModificationVersion):
                    inst = self._store.find_version(mv.name, mv.version)
                    if inst:
                        versions.append(inst)

        # Selected addons
        for i in range(self.addons_list.count()):
            item = self.addons_list.item(i)
            if item and item.isSelected():
                mv = item.data(Qt.ItemDataRole.UserRole)
                if isinstance(mv, ModificationVersion):
                    inst = self._store.find_version(mv.name, mv.version)
                    if inst:
                        versions.append(inst)

        # Selected helper executables. These are the only entries allowed to
        # replace .exe/.dll in the game folder, which is the whole point of
        # them (ModdedExe, Gentools' d3d8.dll, Generals Online).
        for i in range(self.exes_list.count()):
            item = self.exes_list.item(i)
            if item and item.isSelected():
                mv = item.data(Qt.ItemDataRole.UserRole)
                if isinstance(mv, ModificationVersion):
                    inst = self._store.find_version(mv.name, mv.version)
                    if inst:
                        versions.append(inst)

        # Deduplicate: the same version can be reachable from two tabs.
        seen: set[tuple[str, str, str]] = set()
        unique: list[ModificationVersion] = []
        for mv in versions:
            key = (mv.name.casefold(), mv.version, mv.mod_type.value)
            if key not in seen:
                seen.add(key)
                unique.append(mv)
        return unique

    def _resolve_install(self):
        from ..core import steam as steam_mod
        from ..core.models import GameInstall

        if self._settings.game_dir:
            d = Path(self._settings.game_dir).expanduser()
            if not d.is_dir():
                raise FileNotFoundError(f"Game directory not found: {d}")
            mode = steam_mod.detect_mode(d) or self._settings.game
            install = GameInstall(game_dir=d, mode=mode)
            # The user pointed at a folder directly; recover the Steam bits by
            # matching it against what Steam reports, so launching still works.
            for found in steam_mod.find_game_dirs():
                if found.game_dir.resolve() == d.resolve():
                    install.appid = found.appid
                    install.steam_root = found.steam_root
                    install.library = found.library
                    break
            return install

        installs = steam_mod.find_game_dirs()
        if not installs:
            raise FileNotFoundError(
                "No C&C Generals / Zero Hour install found.\n"
                "Set the game path in Options."
            )
        # Both titles share the same Steam folder on Linux.  pick_install
        # finds the best match and falls back rather than returning nothing.
        install = steam_mod.pick_install(installs, self._settings.game)
        if install is None:  # unreachable: the empty case is handled above
            raise FileNotFoundError("No C&C Generals / Zero Hour install found.")
        # On Steam both games live in one folder, so the detected mode is
        # always "zh" (ZH markers are found first).  Honour the user's
        # dropdown choice so exe resolution picks the right binary.
        if install.mode != self._settings.game:
            log.info(
                "dropdown says %r but detected install is %r — "
                "using dropdown choice for exe selection",
                self._settings.game,
                install.mode,
            )
            install.mode = self._settings.game
        return install

    def _chosen_game_exe(self, game_dir) -> str | None:
        """The user's chosen game client, or None to keep the default.

        Steam's ``-applaunch`` always starts whatever executable it was told
        to start, so honouring a different pick means running it through Proton
        inside the game's own prefix.
        """
        return self._chosen_executable(
            game_dir, self._settings.game_exe, None
        )

    def _chosen_executable(self, game_dir, stored: str, fallback: Path | None):
        """Resolve a stored selection to a file; fall back when missing.

        Empty selection, a missing file and a stale path all mean "use the
        default", each logged so a quiet switch back to Steam stays visible.
        """
        from ..core import executables as exe_mod

        if not stored:
            return fallback
        path = exe_mod.resolve(game_dir, stored)
        if path is None:
            log.warning(
                "selected executable is missing, using default: %s", stored
            )
            return fallback
        log.info("launching selected executable: %s", path)
        return path

    # ---- UI enable/disable --------------------------------------------------

    def _disable_ui(self):
        for btn in (
            self.launch_btn, self.wb_btn, self.windowed_btn,
            self.quickstart_btn, self.options_btn,
            self.mods_btn, self.patches_btn, self.addons_btn,
            self.add_mod_btn, self.add_from_files_btn, self.exes_btn,
            self.patch_game_btn,
        ):
            btn.setEnabled(False)

    def _enable_ui(self):
        for btn in (
            self.launch_btn, self.wb_btn, self.windowed_btn,
            self.quickstart_btn, self.options_btn,
            self.mods_btn, self.patches_btn, self.addons_btn,
            self.add_mod_btn, self.add_from_files_btn, self.exes_btn,
            self.patch_game_btn,
        ):
            btn.setEnabled(True)

    # ---- community patch -----------------------------------------------------

    def _check_patch_version(self):
        """Background check for latest community patch version."""
        from .workers import PatchCheckWorker
        worker = PatchCheckWorker(game=self._settings.game, parent=self)
        worker.result.connect(self._on_patch_check_result)
        self._patch_check_worker = worker
        worker.start()

    def _on_patch_check_result(self, latest_tag: str):
        """Update patch UI based on background version check."""
        self._patch_check_worker = None
        if not latest_tag:
            return  # network error, leave UI as-is

        self._latest_patch_tag = latest_tag
        current_tag = self._settings.community_patch_tag
        latest_short = latest_tag.replace("weekly-", "")

        if current_tag == latest_tag:
            # Already on latest
            self._patch_status_label.setText(f"Game patched ({latest_short})")
            self._patch_status_label.setStyleSheet("color: #00cc00;")
            self.patch_game_btn.setText("GAME IS PATCHED")
            self.patch_game_btn.setEnabled(False)
        else:
            # Update available (or not patched at all)
            self._patch_status_label.setText(f"New patch available! ({latest_short})")
            self._patch_status_label.setStyleSheet("color: #ffcc00;")
            self.patch_game_btn.setText(f"PATCH NOW ({latest_short})")
            self.patch_game_btn.setEnabled(True)

    def _on_patch_game(self):
        """Download and install the latest TheSuperHackers community patch."""
        try:
            install = self._resolve_install()
        except FileNotFoundError as exc:
            InfoDialog.critical(self, "Error", str(exc))
            return

        tag = self._latest_patch_tag
        if not tag:
            # Background check hasn't finished or failed — do a sync check
            from ..core import patcher
            try:
                release = patcher.fetch_latest_release(self._settings.game)
                tag = release.tag
            except patcher.PatchError as exc:
                InfoDialog.critical(self, "Patch Error", str(exc))
                return

        if self._settings.community_patch_tag == tag:
            InfoDialog.information(
                self, "Already Up To Date",
                f"You already have the latest community patch ({tag}).",
            )
            return

        short = tag.replace("weekly-", "")
        if not InfoDialog.question(
            self, "Patch Game",
            f"Download community patch {short}?\n\n"
            f"This will place the patched exe in your game folder\n"
            f"alongside the original. Your original files will not be modified.",
        ):
            return

        self.patch_game_btn.setEnabled(False)
        self._patch_progress.setRange(0, 0)
        self._patch_progress.setFormat("Downloading...")
        self._patch_progress.show()

        worker = GamePatchWorker(
            install.game_dir,
            self._paths,
            game=self._settings.game,
            parent=self,
        )
        self._patch_worker = worker
        worker.status.connect(self._on_patch_status)
        worker.progress.connect(self._on_patch_progress)
        worker.finished.connect(self._on_patch_finished)
        worker.error.connect(self._on_patch_error)
        worker.start()

    def _on_patch_status(self, msg: str):
        self._patch_progress.setFormat(msg)

    def _on_patch_progress(self, p):
        self._patch_progress.setRange(0, 100)
        pct = int(p.fraction * 100)
        self._patch_progress.setValue(pct)
        text = f"{p.megabytes} / {p.total_megabytes} MB"
        if p.speed_str:
            text += f"  ({p.speed_str})"
        self._patch_progress.setFormat(text)

    def _on_patch_finished(self, tag: str):
        self._patch_worker = None
        self._patch_progress.hide()
        short = tag.replace("weekly-", "")

        self._settings.community_patch_tag = tag
        self._settings.game_exe = "generalszh.exe"
        self._settings.save()

        # Update UI to "patched" state
        self._patch_status_label.setText(f"Game patched ({short})")
        self._patch_status_label.setStyleSheet("color: #00cc00;")
        self.patch_game_btn.setText("GAME IS PATCHED")
        self.patch_game_btn.setEnabled(False)
        self._patch_version_label.setText(f"Game patch: {short}")

        InfoDialog.information(
            self, "Patch Installed",
            f"Community patch {tag} installed.\n\n"
            f"generalszh.exe is now set as your game executable.",
        )

    def _on_patch_error(self, error: str):
        self._patch_worker = None
        self._patch_progress.hide()
        self.patch_game_btn.setEnabled(True)
        InfoDialog.critical(self, "Patch Error", error)

    # ---- button toggles -----------------------------------------------------

    def _toggle_windowed(self):
        self._settings.windowed = not self._settings.windowed
        self._settings.save()
        self._update_windowed_btn()

    def _update_windowed_btn(self):
        if self._settings.windowed:
            self.windowed_btn.setText("CHANGE TO FULL SCREEN")
        else:
            self.windowed_btn.setText("CHANGE TO WINDOWED")

    def _toggle_quickstart(self):
        self._settings.quickstart = not self._settings.quickstart
        self._settings.save()
        self._update_quickstart_btn()

    def _update_quickstart_btn(self):
        if self._settings.quickstart:
            self.quickstart_btn.setText("CHANGE TO NORMAL START")
        else:
            self.quickstart_btn.setText("CHANGE TO QUICK START")

    # ---- options dialog -----------------------------------------------------

    def _open_options(self):
        from .options_window import OptionsWindow

        dlg = OptionsWindow(self._settings, parent=self)
        dlg.exec()
        # Reload settings after options close
        self._settings = Settings.load(self._paths)
        self._update_windowed_btn()
        self._update_quickstart_btn()

    # ---- add mod dialogs ----------------------------------------------------

    def _on_add_mod(self):
        """Open dialog to add a mod from the catalog."""
        from .add_mod_window import AddModWindow

        if not self._catalog:
            InfoDialog.information(
                self, "No Catalog",
                "Catalog not loaded. Cannot show available mods.",
            )
            return

        # Get installed mod names
        installed_names = {
            entry.mod_name.casefold()
            for entry in (self._catalog.mod_datas or [])
            if self._store.get(entry.mod_name) is not None
        }

        # Filter to uninstalled mods
        available = [
            entry.mod_name
            for entry in (self._catalog.mod_datas or [])
            if entry.mod_name.casefold() not in installed_names
        ]

        if not available:
            InfoDialog.information(
                self, "All Installed",
                "All mods from the catalog are already installed.",
            )
            return

        dlg = AddModWindow(available, parent=self)
        if dlg.exec():
            mod_name = dlg.selected_mod
            mv = self._catalog_mods.get(mod_name.casefold())
            if mv:
                self._on_update_mod(mv)

    def _manual_add_mod_type(self) -> ModType:
        """Which kind of entry the visible list is asking for.

        The button text says "Add patch from files" on the Patches tab, but the
        handler used to hardcode ModType.MOD, so manual patches landed in the
        store as full mods.
        """
        return {
            0: ModType.MOD,
            1: ModType.PATCH,
            2: ModType.ADDON,
            3: ModType.EXECUTABLE,
        }.get(self._list_stack.currentIndex(), ModType.MOD)

    def _on_add_from_files(self):
        """Open file dialog to add a mod from local archives."""
        from PySide6.QtWidgets import QFileDialog

        from .manual_add_window import ManualAddModWindow

        files, _ = QFileDialog.getOpenFileNames(
            self,
            "Select mod archives",
            "",
            "Archives and files (*.big *.7z *.zip *.rar *.tar *.gz *.exe *.dll);;"
            "Folders;;All files (*)",
        )
        if not files:
            return

        mod_type = self._manual_add_mod_type()
        dlg = ManualAddModWindow(files, parent=self)

        def on_confirmed(name: str, version: str):
            from ..core.installer import Installer

            mv = ModificationVersion(
                name=name,
                version=version,
                mod_type=mod_type,
            )
            # Patches/addons/exes belong to the selected mod, otherwise the
            # linker cannot match them to their dependency.
            if mod_type in (ModType.PATCH, ModType.ADDON, ModType.EXECUTABLE):
                if self._selected_mod:
                    mv.dependence_name = (
                        self._selected_mod.dependence_name
                        or self._selected_mod.mod_name
                    )

            installer = Installer(self._paths.mods_dir)
            try:
                # One staging area for all selected files: install() per file
                # wiped the previous one, leaving only the last.
                final = installer.install_many(
                    mv, [Path(f) for f in files],
                    progress=lambda m: log.info(m),
                )

                mv.installed = True
                self._store.upsert(mv, self._paths.mods_dir)
                self._store = ModStore(self._paths.data_dir)
                self._refresh_mod_list()
                InfoDialog.information(
                    self, "Installed",
                    f"{name} {version} installed successfully "
                    f"({len(files)} file{'s' if len(files) != 1 else ''}) "
                    f"to {final.name}.",
                )
            except Exception as exc:
                InfoDialog.critical(
                    self, "Install Error",
                    f"Failed to install {name}: {exc}",
                )

        dlg.confirmed.connect(on_confirmed)
        dlg.exec()

    # ---- context menu -------------------------------------------------------

    def _mod_context_menu(self, pos):
        item = self.mods_list.itemAt(pos)
        if not item:
            return
        entry = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(entry, RawModEntry):
            return

        menu = QMenu(self)
        menu.addAction("Open mod folder", lambda: self._open_mod_folder(entry))
        menu.addAction("Open game folder", lambda: self._open_game_folder())
        menu.addAction("Open maps folder", lambda: self._open_maps_folder())
        menu.addAction("Open replays folder", lambda: self._open_replays_folder())
        menu.addSeparator()

        mv = self._catalog_mods.get(entry.mod_name.casefold())
        if mv:
            if mv.moddb_link:
                menu.addAction("Visit ModDB page", lambda: webbrowser.open(mv.moddb_link))
            if mv.discord_link:
                menu.addAction("Join Discord server", lambda: webbrowser.open(mv.discord_link))

            menu.addSeparator()
            overridden = self._link_overrides.get(mv.name, mv.version)
            label = (
                "Clear custom download link"
                if overridden
                else "Set download link…"
            )
            menu.addAction(
                label, lambda: self._edit_download_link(mv, entry)
            )

        menu.addSeparator()
        menu.addAction("Check mod integrity", lambda: self._check_mod_integrity(entry))
        menu.addAction("Delete mod", lambda: self._delete_mod(entry))

        menu.exec(self.mods_list.mapToGlobal(pos))

    def _addon_context_menu(self, list_widget: QListWidget, pos):
        """Right-click menu for the patch/addon/executable lists.

        Those lists set ``CustomContextMenu`` but never connected a handler, so
        right-clicking did nothing. These entries live in the same store as
        mods, so removing one is the same operation.
        """
        item = list_widget.itemAt(pos)
        if not item:
            return
        mv = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(mv, ModificationVersion):
            return

        installed = self._store.get(mv.name)
        is_installed = installed is not None and installed.latest is not None

        menu = QMenu(self)
        if is_installed:
            menu.addAction(
                f"Delete {mv.mod_type.value.lower()}",
                lambda: self._delete_addon(mv),
            )
        else:
            placeholder = menu.addAction("Not installed")
            placeholder.setEnabled(False)

        menu.exec(list_widget.mapToGlobal(pos))

    def _delete_addon(self, mv: ModificationVersion):
        """Uninstall an installed patch/addon/executable after confirmation."""
        installed = self._store.get(mv.name)
        if not installed or not installed.latest:
            InfoDialog.information(self, "Not installed", f"{mv.name} is not installed.")
            return

        kind = mv.mod_type.value
        if not InfoDialog.question(
            self,
            f"Delete {kind}",
            f"Are you sure you want to delete {mv.name}?\n"
            "All installed versions will be removed.",
            continue_text="Delete",
            default_button=False,
        ):
            return

        self._store.remove_mod(mv.name, self._paths.mods_dir, delete_files=True)
        self._store = ModStore(self._paths.data_dir)  # reload

        self._refresh_patches_list()
        self._refresh_addons_list()
        self._refresh_exes_list()

    @staticmethod
    def _manifest_url(entry: RawModEntry) -> str:
        return entry.mod_link or ""

    def _edit_download_link(self, mv: ModificationVersion, entry: RawModEntry):
        """Let the user replace a catalog link that no longer serves a file."""
        from .link_override_window import LinkOverrideDialog

        current = self._link_overrides.get(mv.name, mv.version)
        dlg = LinkOverrideDialog(mv.name, mv.version, current, parent=self)
        dlg.exec()
        if dlg.result() != dlg.DialogCode.Accepted:
            return

        if dlg.removed:
            self._link_overrides.clear(mv.name, mv.version)
            # Fall back to whatever the catalog itself says.
            ok = self._reload_from_catalog_links(mv, self._manifest_url(entry))
            self._refresh_mod_list()
            InfoDialog.information(
                self, "Link cleared",
                f"{mv.name} will use the catalog link again."
                if ok
                else f"Override removed for {mv.name}.",
            )
            return

        url = dlg.url
        self._link_overrides.set(mv.name, mv.version, url)
        # Apply immediately so the user can retry without restarting.
        self._link_overrides.apply(mv)
        self._refresh_mod_list()
        InfoDialog.information(
            self, "Link saved",
            f"{mv.name} {mv.version} will download from your link.",
        )

    def _reload_from_catalog_links(self, mv: ModificationVersion, manifest_url: str = ""):
        """Drop a user override and put the catalog's own URL back in place."""
        try:
            from ..core.catalog import CatalogClient

            if manifest_url:
                with CatalogClient(
                    cache_dir=self._paths.cache_dir,
                    ttl=self._settings.catalog_ttl,
                ) as client:
                    fresh = client.load_mod_manifest(manifest_url)
                if fresh and fresh.simple_download_link:
                    mv.simple_download_link = fresh.simple_download_link
                    return True
        except Exception as exc:
            log.warning("Could not restore catalog link for %s: %s", mv.name, exc)
        return False

    def _open_mod_folder(self, entry: RawModEntry):
        import subprocess

        installed = self._store.get(entry.mod_name)
        if installed and installed.latest:
            folder = installed.latest.folder_name(self._paths.mods_dir)
            if folder.is_dir():
                subprocess.Popen(["xdg-open", str(folder)])
                return
        InfoDialog.information(self, "Not installed", "Install the mod first.")

    def _open_game_folder(self):
        import subprocess
        try:
            install = self._resolve_install()
            subprocess.Popen(["xdg-open", str(install.game_dir)])
        except FileNotFoundError as exc:
            InfoDialog.warning(self, "Error", str(exc))

    def _open_maps_folder(self):
        self._open_proton_user_folder("Maps")

    def _open_replays_folder(self):
        self._open_proton_user_folder("Replays")

    def _open_proton_user_folder(self, subfolder: str):
        """Open a subfolder under the game's user data in the Proton prefix."""
        import subprocess

        from ..core.options_ini import find_options_ini

        ini = find_options_ini(game=self._settings.game)
        if ini and ini.parent.is_dir():
            target = ini.parent / subfolder
            target.mkdir(parents=True, exist_ok=True)
            subprocess.Popen(["xdg-open", str(target)])
        else:
            InfoDialog.warning(
                self, "Not Found",
                f"Cannot locate {subfolder} folder.\n"
                "The game may not have been run yet via Proton.",
            )

    def _check_mod_integrity(self, entry: RawModEntry):
        """Run integrity check on a single mod and show the result."""
        installed = self._store.get(entry.mod_name)
        if not installed or not installed.latest:
            InfoDialog.information(self, "Not installed", "Install the mod first.")
            return

        folder = installed.latest.folder_name(self._paths.mods_dir)
        if not folder.is_dir():
            InfoDialog.information(self, "Not installed", "Mod folder not found.")
            return

        # Show a progress dialog while hashing
        from PySide6.QtWidgets import QProgressDialog
        progress = QProgressDialog(
            f"Checking {entry.mod_name}...", "Cancel", 0, 0, self
        )
        progress.setWindowTitle("Integrity Check")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.show()

        worker = IntegrityWorker([folder], parent=self)

        def on_checked(result):
            progress.close()
            if result.no_baseline:
                InfoDialog.information(
                    self, "Integrity Check",
                    f"No baseline recorded for {entry.mod_name}.\n"
                    "A baseline has been created for future checks.",
                )
            elif result.ok:
                InfoDialog.information(
                    self, "Integrity Check",
                    f"{entry.mod_name} — all files OK.",
                )
            else:
                review = IntegrityReviewDialog(result, parent=self)
                review.exec()

        def on_cancelled():
            worker.checked.disconnect(on_checked)
            worker.requestInterruption()

        progress.canceled.connect(on_cancelled)
        worker.checked.connect(on_checked)
        worker.start()

    def _delete_mod(self, entry: RawModEntry):
        """Delete an installed mod after user confirmation."""
        installed = self._store.get(entry.mod_name)
        if not installed or not installed.latest:
            InfoDialog.information(self, "Not installed", "This mod is not installed.")
            return

        if not InfoDialog.question(
            self,
            "Delete Mod",
            f"Are you sure you want to delete {entry.mod_name}?\n"
            "All installed versions will be removed.",
            continue_text="Delete",
            default_button=False,
        ):
            return

        # Remove mod and all its files
        self._store.remove_mod(entry.mod_name, self._paths.mods_dir, delete_files=True)
        self._store = ModStore(self._paths.data_dir)  # reload

        # Refresh list
        self._refresh_mod_list()

    # ---- links from mod buttons ---------------------------------------------

    def _on_changelog(self, mv: ModificationVersion):
        if mv.news_link:
            webbrowser.open(mv.news_link)

    def _on_support(self, mv: ModificationVersion):
        if mv.support_link:
            webbrowser.open(mv.support_link)

    # ---- update launcher -----------------------------------------------------

    def _check_launcher_version(self):
        """Background check for a newer GenLauncher-LP release on GitHub."""
        worker = LauncherUpdateCheckWorker(parent=self)
        worker.result.connect(self._on_launcher_check_result)
        self._launcher_check_worker = worker
        worker.start()

    def _on_launcher_check_result(self, latest_tag: str, current: str):
        """Show or hide the update button based on version comparison."""
        self._launcher_check_worker = None
        if not latest_tag or latest_tag == current:
            self.update_launcher_btn.hide()
            return
        self._latest_launcher_tag = latest_tag
        self.update_launcher_btn.setText(f"UPDATE TO v{latest_tag}")
        self.update_launcher_btn.show()

    def _on_update_launcher(self):
        """Run git pull + pip install to update the launcher."""
        import subprocess
        import sys

        latest_tag = self._latest_launcher_tag
        if not latest_tag:
            return

        self.update_launcher_btn.setEnabled(False)
        self.update_launcher_btn.setText("Updating...")
        project_dir = Path(__file__).resolve().parent.parent.parent.parent

        # ── Step 1: git pull ──
        try:
            subprocess.run(
                ["git", "pull", "--ff-only", "origin", "main"],
                cwd=str(project_dir), check=True, capture_output=True, text=True,
            )
        except FileNotFoundError:
            InfoDialog.critical(
                self, "Update Failed",
                "git is not installed.\n\n"
                "Install git and try again, or update manually:\n"
                "  cd ~/GenLauncher-LP && git pull",
            )
            self.update_launcher_btn.setEnabled(True)
            return
        except subprocess.CalledProcessError as exc:
            stderr = (exc.stderr or "").strip()
            if "divergent" in stderr or "not possible to fast-forward" in stderr:
                msg = (
                    "Your local files have been modified.\n\n"
                    "To update, run manually in a terminal:\n"
                    "  cd ~/GenLauncher-LP\n"
                    "  git stash && git pull && git stash pop"
                )
            else:
                msg = f"git pull failed:\n{stderr}" if stderr else "git pull failed."
            InfoDialog.critical(self, "Update Failed", msg)
            self.update_launcher_btn.setEnabled(True)
            return

        # ── Step 2: pip install — try multiple approaches ──
        venv_pip = project_dir / ".venv" / "bin" / "pip"
        pip_commands = []
        if venv_pip.is_file():
            pip_commands.append(
                [str(venv_pip), "install", "-e", str(project_dir), "-q"],
            )
        pip_commands.append(
            [sys.executable, "-m", "pip", "install", "-e", str(project_dir),
             "--break-system-packages", "-q"],
        )
        pip_commands.append(
            ["pip3", "install", "-e", str(project_dir),
             "--break-system-packages", "-q"],
        )

        pip_ok = False
        for cmd in pip_commands:
            try:
                subprocess.run(cmd, check=True, capture_output=True, text=True)
                pip_ok = True
                break
            except (subprocess.CalledProcessError, FileNotFoundError):
                continue

        if pip_ok:
            InfoDialog.success(
                self, "Updated",
                f"GenLauncher-LP updated to v{latest_tag}.\n\n"
                "Restart the launcher to use the new version.",
            )
        else:
            log.warning("pip install failed — code was pulled but not reinstalled")
            InfoDialog.warning(
                self, "Partially Updated",
                f"Code updated to v{latest_tag}, but package "
                "reinstall failed (pip not available).\n\n"
                "The update should still work after a restart.\n"
                "If not, run in a terminal:\n"
                "  cd ~/GenLauncher-LP && .venv/bin/pip install -e .",
            )

        self.update_launcher_btn.hide()

    # ---- about / version ----------------------------------------------------

    def _copy_version(self, ver: str):
        """Copy version string to clipboard with brief visual feedback."""
        QApplication.clipboard().setText(f"GenLauncher-LP v{ver}")
        old = self._version_label.text()
        self._version_label.setText("Copied!")
        self._version_label.setStyleSheet(
            f"color: {DEFAULT_COLORS.active}; padding: 2px 0;"
        )
        QTimer.singleShot(1500, lambda: (
            self._version_label.setText(old),
            self._version_label.setStyleSheet(
                f"color: {DEFAULT_COLORS.inactive_border}; padding: 2px 0;"
            ),
        ))

    def _on_about(self):
        from .about_dialog import AboutDialog
        dlg = AboutDialog(parent=self)
        dlg.exec()

    # ---- game selector -------------------------------------------------------

    def _populate_game_combo(self):
        """Fill game dropdown with both titles (they share the same install folder)."""
        GAME_SHORT = {"zh": "Zero Hour", "generals": "Generals"}

        self._game_combo.blockSignals(True)
        self._game_combo.clear()

        modes = ["zh", "generals"]
        current_idx = 0
        for i, mode in enumerate(modes):
            self._game_combo.addItem(GAME_SHORT.get(mode, mode), userData=mode)
            if mode == self._settings.game:
                current_idx = i

        self._game_combo.setCurrentIndex(current_idx)
        self._game_combo.blockSignals(False)

    def _on_game_changed(self, index: int):
        """Switch to a different game title."""
        mode = self._game_combo.currentData()
        if not mode or mode == self._settings.game:
            return

        self._settings.game = mode
        self._settings.save()

        # Reload everything for the new game
        InfoDialog.information(
            self, "Game Changed",
            f"Switched to {self._game_combo.currentText()}.\n\n"
            f"The launcher will now reload.",
        )

        # Re-trigger catalog load for the new game
        self._catalog = None
        self._catalog_mods = {}
        self._catalog_patches = []
        self._catalog_addons = []
        self._catalog_exes = []
        self.start_catalog_load()

    # ---- warnings -----------------------------------------------------------

    def _show_warnings(self, result: CatalogLoadResult):
        if result.warnings:
            for w in result.warnings[:3]:
                log.warning("catalog: %s", w)

    # ---- close event --------------------------------------------------------

    def _unlink_pending_session(self, event) -> None:
        """Undo any half-finished link session before the window goes away.

        The journal is the only record of what we changed in the game folder.
        If it is non-empty on shutdown, something was linked and never unlinked
        (crash, kill, or closing during the link), so the game would be left
        with our symlinks and parked .GLR files.
        """
        if not self._paths.journal_file.exists():
            return
        try:
            install = self._resolve_install()
        except Exception as exc:
            log.warning("Cannot resolve install for cleanup on close: %s", exc)
            return

        try:
            from ..core.linker import Linker, LinkStrategy

            linker = Linker(
                install.game_dir,
                self._paths.journal_file,
                self._paths.mods_dir,
                strategy=LinkStrategy.parse(self._settings.link_strategy),
                game_files=set(GAME_FILES),
            )
            report = linker.unlink()
            if report.errors:
                log.warning("Incomplete cleanup on close: %s", report.errors[:3])
            else:
                log.info("Cleaned up pending link session on close")
        except Exception as exc:
            log.error("Cleanup on close failed: %s", exc)

    def closeEvent(self, event):
        if self._is_game_running:
            event.ignore()
            InfoDialog.information(
                self, "Game Running",
                "The game is still running. Close the game first.",
            )
            return

        # Cancel any active downloads and wait for the threads to actually
        # finish. cancel() only requests; returning while a QThread is still
        # running triggers "QThread: Destroyed while thread is still running"
        # and aborts the process, so the window must block until they are gone.
        for worker in list(self._active_downloads.values()):
            worker.cancel()
        for worker in list(self._active_downloads.values()):
            if not worker.wait(15000):
                log.warning("Download worker did not stop in time; still waiting")
                worker.wait()
        self._active_downloads.clear()

        if self._integrity_worker is not None:
            self._integrity_worker.wait(15000)
            self._integrity_worker = None

        if self._patch_worker is not None:
            self._patch_worker.cancel()
            self._patch_worker.wait(15000)
            self._patch_worker = None

        # A link that is still in flight owns the game folder; unlink it so we
        # do not leave symlinks behind, then restore any camera patch.
        self._unlink_pending_session(event)
        self._restore_camera_patch()

        self._settings.save()
        event.accept()
