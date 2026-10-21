"""Custom list-item widgets for mods, patches, addons, and executables.

Layout and sizing mirrors Rewritten's data templates:
  - ModItemWidget    ≈ ModificationTemplate  (550px, with 500×100 image)
  - AddonItemWidget  ≈ AddonsTemplate        (550px, no image)

Both use absolute positioning inside a fixed-size QWidget for pixel-perfect
margin layout.
"""

from __future__ import annotations

import webbrowser
from pathlib import Path

from PySide6.QtCore import (
    QPropertyAnimation,
    QSequentialAnimationGroup,
    Qt,
    Signal,
)
from PySide6.QtGui import QFont, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..core.models import ModificationVersion
from .theme import ui_font, DEFAULT_COLORS


# ---------------------------------------------------------------------------
# StarRating — clickable 5-star widget
# ---------------------------------------------------------------------------

class StarRating(QWidget):
    """Clickable 5-star rating bar."""

    rating_changed = Signal(int)  # 0-5

    STAR_SIZE = 16

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rating = 0
        self._stars: list[QLabel] = []
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(1)
        for i in range(5):
            star = QLabel("☆", self)
            star.setFont(QFont("", self.STAR_SIZE))
            star.setFixedSize(self.STAR_SIZE + 4, self.STAR_SIZE + 4)
            star.setAlignment(Qt.AlignmentFlag.AlignCenter)
            star.setStyleSheet(f"color: {DEFAULT_COLORS.inactive_border};")
            star.setCursor(Qt.CursorShape.PointingHandCursor)
            star.mousePressEvent = lambda ev, idx=i: self._on_click(idx)
            layout.addWidget(star)
            self._stars.append(star)
        self.setFixedSize((self.STAR_SIZE + 5) * 5, self.STAR_SIZE + 4)

    def _on_click(self, idx: int):
        new_rating = idx + 1
        if new_rating == self._rating:
            new_rating = 0  # click same star again to clear
        self.set_rating(new_rating)
        self.rating_changed.emit(self._rating)

    def set_rating(self, rating: int):
        self._rating = max(0, min(5, rating))
        for i, star in enumerate(self._stars):
            if i < self._rating:
                star.setText("★")
                star.setStyleSheet(f"color: {DEFAULT_COLORS.active};")
            else:
                star.setText("☆")
                star.setStyleSheet(f"color: {DEFAULT_COLORS.inactive_border};")

    def rating(self) -> int:
        return self._rating


# ---------------------------------------------------------------------------
# Blink animation helper
# ---------------------------------------------------------------------------

def make_blink_animation(
    target: QWidget,
    count: int = 7,
    duration_ms: int = 500,
) -> QPropertyAnimation | None:
    """Create an opacity blink animation on *target*.

    Opacity pulses to 0.5 and back, repeating *count* times at 500 ms each
    direction.
    """
    effect = target.graphicsEffect()
    if not isinstance(effect, QGraphicsOpacityEffect):
        effect = QGraphicsOpacityEffect(target)
        target.setGraphicsEffect(effect)

    anim = QPropertyAnimation(effect, b"opacity", target)
    anim.setDuration(duration_ms)
    anim.setStartValue(1.0)
    anim.setEndValue(0.5)
    anim.setLoopCount(count * 2)  # each loop is one direction
    # QPropertyAnimation lacks AutoReverse, so we double the loop count and
    # let it snap back.
    return anim


# ---------------------------------------------------------------------------
# ModItemWidget — full mod entry with image
# ---------------------------------------------------------------------------

class ModItemWidget(QWidget):
    """One mod in the mods list.

    Grid: 4 cols (310 / 1* / 1* / 40), 6 rows (23/23/50/50/30/33).
    Total width: 550px, total height: 23+23+50+50+30+33 = 209px.
    """

    update_clicked = Signal(ModificationVersion)
    changelog_clicked = Signal(ModificationVersion)
    support_clicked = Signal(ModificationVersion)
    version_changed = Signal(ModificationVersion, str)  # mv, new_version
    rating_changed = Signal(str, int)  # mod_name, new_rating (0-5)

    WIDGET_WIDTH = 550
    WIDGET_HEIGHT = 168

    def __init__(self, mv: ModificationVersion, parent=None):
        super().__init__(parent)
        self.mv = mv
        self.setFixedSize(self.WIDGET_WIDTH, self.WIDGET_HEIGHT)
        self._build_ui()
        self._populate()

    def _build_ui(self):
        # Row 0: Mod name + version (single visible yellow row)
        self.name_label = QLabel(self)
        self.name_label.setGeometry(10, 0, 300, 22)
        self.name_label.setFont(ui_font(11, bold=True))
        self.name_label.setStyleSheet(f"color: {DEFAULT_COLORS.active};")
        self.name_label.setWordWrap(False)

        # Row 0: Star rating (top right)
        self.star_rating = StarRating(self)
        self.star_rating.setGeometry(440, 2, 105, 20)
        self.star_rating.rating_changed.connect(
            lambda r: self.rating_changed.emit(self.mv.name, r)
        )

        # Row 0-1, col 1-3: Version combobox (hidden by default)
        self.version_combo = QComboBox(self)
        self.version_combo.setGeometry(310, 0, 125, 28)
        self.version_combo.hide()
        self.version_combo.currentTextChanged.connect(self._on_version_changed)

        # Row 1: Latest version — folded into the name row on one line
        self.version_label = QLabel(self)
        self.version_label.setGeometry(10, 22, 300, 20)
        self.version_label.setFont(ui_font(11))
        self.version_label.setStyleSheet(f"color: {DEFAULT_COLORS.inactive_border};")
        self.version_label.hide()

        # Row 2: Image (y=22, h=100)
        self.image_label = QLabel(self)
        self.image_label.setGeometry(25, 22, 500, 100)
        self.image_label.setStyleSheet(
            f"border: 2px solid {DEFAULT_COLORS.inactive_border}; background: {DEFAULT_COLORS.dark_bg};"
        )
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.setScaledContents(True)

        # Row 2: Update overlay (for blink animation)
        self.update_overlay = QWidget(self)
        self.update_overlay.setGeometry(25, 22, 500, 100)
        self.update_overlay.setStyleSheet(
            f"background: qlineargradient("
            f"x1:0, y1:0, x2:1, y2:0, "
            f"stop:0 {DEFAULT_COLORS.button_selection}, "
            f"stop:0.35 transparent, "
            f"stop:0.65 transparent, "
            f"stop:1 {DEFAULT_COLORS.button_selection});"
        )
        self.update_overlay.hide()
        self._blink_anim = None

        # Row 3: ProgressBar (y=124, h=18)
        self.progress_bar = QProgressBar(self)
        self.progress_bar.setGeometry(10, 124, 530, 18)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setValue(0)
        self.progress_bar.hide()

        # Row 3: Info text over progress bar
        self.info_label = QLabel(self)
        self.info_label.setGeometry(25, 124, 500, 18)
        self.info_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.info_label.setFont(ui_font(9, bold=True))
        self.info_label.setStyleSheet(f"color: {DEFAULT_COLORS.download_text};")
        self.info_label.hide()

        # Row 4: Buttons (y=138, h=28)
        btn_y = 138

        self.update_btn = QPushButton("Update!", self)
        self.update_btn.setGeometry(5, btn_y, 125, 28)
        self.update_btn.setFont(ui_font(11))
        self.update_btn.clicked.connect(lambda: self.update_clicked.emit(self.mv))

        self.support_btn = QPushButton("Donate", self)
        self.support_btn.setGeometry(138, btn_y, 90, 28)
        self.support_btn.setFont(ui_font(10))
        self.support_btn.clicked.connect(lambda: self.support_clicked.emit(self.mv))

        self.network_btn = QPushButton("How to play online?", self)
        self.network_btn.setGeometry(236, btn_y, 140, 28)
        self.network_btn.setFont(ui_font(10))
        self.network_btn.hide()

        self.changelog_btn = QPushButton("Changelog", self)
        self.changelog_btn.setGeometry(455, btn_y, 90, 28)
        self.changelog_btn.setFont(ui_font(10))
        self.changelog_btn.clicked.connect(
            lambda: self.changelog_clicked.emit(self.mv)
        )

    def _populate(self):
        mv = self.mv
        self.name_label.setText(f"{mv.name} version {mv.version}")

        # Hide buttons that have no link
        if not mv.support_link:
            self.support_btn.hide()
        if not mv.network_info:
            self.network_btn.hide()
        else:
            self.network_btn.show()
            self.network_btn.clicked.connect(
                lambda: webbrowser.open(mv.network_info) if mv.network_info else None
            )
        if not mv.news_link:
            self.changelog_btn.hide()

    def set_update_state(self, installed: bool, has_update: bool):
        """Update the button text based on install/update status.

        States:
          - "Install"    — not installed at all
          - "Update!"    — newer version available
          - "Installed"  — current version matches latest
        Download states (STOP/Cancelled) handled by set_download_mode().
        """
        if not installed:
            self.update_btn.setText("Install")
        elif has_update:
            self.update_btn.setText("Update!")
        else:
            self.update_btn.setText("Installed")
            self.update_btn.setEnabled(False)

    def set_image(self, pixmap: QPixmap):
        """Set the 500×100 mod banner image."""
        self.image_label.setPixmap(pixmap)

    def set_placeholder(self, text: str = "No preview"):
        """Show a stand-in when the mod has no usable banner image.

        Many catalog entries point at imgur/moddb URLs that rate-limit or 404,
        and one points at a host that no longer exists. An empty bordered box
        reads as a bug, so label it instead.
        """
        self.image_label.setPixmap(QPixmap())
        self.image_label.setText(text)
        f = self.image_label.font()
        f.setPointSize(9)
        self.image_label.setFont(f)

    def set_versions(self, versions: list[str]):
        """Populate the version dropdown."""
        self.version_combo.blockSignals(True)
        self.version_combo.clear()
        self.version_combo.addItems(versions)
        if self.mv.version in versions:
            self.version_combo.setCurrentText(self.mv.version)
        self.version_combo.blockSignals(False)
        if len(versions) > 1:
            self.version_combo.show()

    def show_progress(self, value: int, text: str = ""):
        """Show download progress bar with overlay text."""
        self.progress_bar.show()
        self.progress_bar.setValue(value)
        if text:
            self.info_label.setText(text)
            self.info_label.show()

    def hide_progress(self):
        self.progress_bar.hide()
        self.progress_bar.setValue(0)
        self.info_label.hide()

    def start_blink(self, count: int = 7):
        """Start the update-available blink animation on the overlay."""
        self.update_overlay.show()
        self._blink_anim = make_blink_animation(self.update_overlay, count)
        if self._blink_anim:
            self._blink_anim.finished.connect(self.update_overlay.hide)
            self._blink_anim.start()

    def stop_blink(self):
        if self._blink_anim:
            self._blink_anim.stop()
            self._blink_anim = None
        self.update_overlay.hide()

    def set_download_mode(self, active: bool):
        """Switch Update button to Stop mode during download."""
        if active:
            self.update_btn.setText("STOP")
            self.update_btn.setEnabled(True)
        else:
            self.update_btn.setText("Update!")
            self.update_btn.setEnabled(True)
            self.hide_progress()

    def _on_version_changed(self, text: str):
        if text:
            self.version_changed.emit(self.mv, text)


# ---------------------------------------------------------------------------
# AddonItemWidget — patch/addon/exe entry (no image)
# ---------------------------------------------------------------------------

class AddonItemWidget(QWidget):
    """One patch/addon/executable in their respective lists.

    Grid: 4 cols (310 / 1* / 1* / 40), 4 rows (23/23/30/33).
    Total width: 550px, total height: 23+23+30+33 = 109px.
    """

    update_clicked = Signal(ModificationVersion)
    changelog_clicked = Signal(ModificationVersion)
    rating_changed = Signal(str, int)  # mod_name, new_rating (0-5)

    WIDGET_WIDTH = 550
    WIDGET_HEIGHT = 109

    def __init__(self, mv: ModificationVersion, parent=None):
        super().__init__(parent)
        self.mv = mv
        self._blink_anim: QPropertyAnimation | None = None
        self.setFixedSize(self.WIDGET_WIDTH, self.WIDGET_HEIGHT)
        self._build_ui()
        self._populate()

    def _build_ui(self):
        # Row 0: Name + version (single visible yellow row)
        self.name_label = QLabel(self)
        self.name_label.setGeometry(10, 0, 400, 23)
        self.name_label.setFont(ui_font(9, bold=True))  # FontSize 13
        self.name_label.setStyleSheet(f"color: {DEFAULT_COLORS.active};")
        self.name_label.setWordWrap(False)

        # Row 0: Star rating (top right)
        self.star_rating = StarRating(self)
        self.star_rating.setGeometry(440, 2, 105, 20)
        self.star_rating.rating_changed.connect(
            lambda r: self.rating_changed.emit(self.mv.name, r)
        )

        # Row 0-1, col 1-3: Version combobox
        self.version_combo = QComboBox(self)
        self.version_combo.setGeometry(310, 0, 125, 30)
        self.version_combo.hide()

        # Row 1: Version — folded into the name row
        self.version_label = QLabel(self)
        self.version_label.setGeometry(10, 23, 300, 23)
        self.version_label.setFont(ui_font(11))
        self.version_label.setStyleSheet(f"color: {DEFAULT_COLORS.inactive_border};")
        self.version_label.hide()

        # Row 2: ProgressBar (y=28, h=30 region, bar 20px)
        self.progress_bar = QProgressBar(self)
        self.progress_bar.setGeometry(10, 28, 530, 20)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setValue(0)
        self.progress_bar.hide()

        self.info_label = QLabel(self)
        self.info_label.setGeometry(40, 28, 470, 20)
        self.info_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.info_label.setFont(ui_font(9, bold=True))
        self.info_label.setStyleSheet(f"color: {DEFAULT_COLORS.download_text};")
        self.info_label.hide()

        # Row 3: Buttons (y=76, h=33)
        btn_y = 76

        self.update_btn = QPushButton("Update!", self)
        self.update_btn.setGeometry(5, btn_y, 125, 28)
        self.update_btn.setFont(ui_font(12))
        self.update_btn.clicked.connect(lambda: self.update_clicked.emit(self.mv))

        self.changelog_btn = QPushButton("Changelog", self)
        self.changelog_btn.setGeometry(138, btn_y, 110, 28)
        self.changelog_btn.setFont(ui_font(10))
        self.changelog_btn.clicked.connect(
            lambda: self.changelog_clicked.emit(self.mv)
        )

        self.network_btn = QPushButton("How to play online?", self)
        self.network_btn.setGeometry(256, btn_y, 140, 28)
        self.network_btn.setFont(ui_font(10))
        self.network_btn.hide()

    def _populate(self):
        self.name_label.setText(f"{self.mv.name} version {self.mv.version}")
        if not self.mv.news_link:
            self.changelog_btn.hide()
        if self.mv.network_info:
            self.network_btn.show()

    def set_update_state(self, installed: bool, has_update: bool):
        """Update button text based on install/update status."""
        if not installed:
            self.update_btn.setText("Install")
        elif has_update:
            self.update_btn.setText("Update!")
        else:
            self.update_btn.setText("Installed")
            self.update_btn.setEnabled(False)

    def show_progress(self, value: int, text: str = ""):
        self.progress_bar.show()
        self.progress_bar.setValue(value)
        if text:
            self.info_label.setText(text)
            self.info_label.show()

    def hide_progress(self):
        self.progress_bar.hide()
        self.progress_bar.setValue(0)
        self.info_label.hide()

    def set_download_mode(self, active: bool):
        if active:
            self.update_btn.setText("STOP")
            self.update_btn.setEnabled(True)
        else:
            self.update_btn.setText("Update!")
            self.hide_progress()

    def start_blink(self, count: int = 7):
        """Blink the Update button to flag an available update.

        ModItemWidget blinks an image overlay; these rows have no image, so the
        button itself is the attention cue.
        """
        if self._blink_anim:
            return
        self.update_btn.setEnabled(True)
        self._blink_anim = make_blink_animation(self.update_btn, count)
        if self._blink_anim:
            self._blink_anim.finished.connect(self.stop_blink)
            self._blink_anim.start()

    def stop_blink(self):
        if self._blink_anim:
            self._blink_anim.stop()
            self._blink_anim = None
        effect = self.update_btn.graphicsEffect()
        if isinstance(effect, QGraphicsOpacityEffect):
            effect.setOpacity(1.0)
            self.update_btn.setGraphicsEffect(None)
