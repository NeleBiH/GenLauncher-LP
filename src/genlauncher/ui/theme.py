"""Color palette and QSS stylesheet.

The palette matches Rewritten's theme presets (border, active, dark/light
background, selection and button colors are identical hex values).
"""

from __future__ import annotations

from dataclasses import dataclass

#: Segoe UI is the reference font. On Linux Qt would silently fall back to
#: something unrelated, so these are the metric-similar faces to try, in order.
UI_FONT_FAMILIES = (
    "Segoe UI",
    "Noto Sans",
    "Cantarell",
    "Ubuntu",
    "DejaVu Sans",
    "Liberation Sans",
)


def ui_font(size: int, bold: bool = False) -> "QFont":
    """A QFont that resolves to the closest available UI face on any platform."""
    from PySide6.QtGui import QFont

    font = QFont()
    font.setFamilies(list(UI_FONT_FAMILIES))
    font.setPointSize(size)
    if bold:
        font.setWeight(QFont.Weight.Bold)
    return font


@dataclass
class Colors:
    """All GenLauncher theme colors. Default values match Rewritten's presets."""

    border: str = "#00e3ff"            # GenLauncherBorderColor — cyan
    inactive_border: str = "#a9a9a9"   # GenLauncherInactiveBorder — DarkGray
    inactive_border2: str = "#7a7db0"  # GenLauncherInactiveBorder2 — slate blue
    active: str = "#baff0c"            # GenLauncherActiveColor — lime
    dark_fill: str = "#232977"         # GenLauncherDarkFillColor — dark blue
    dark_bg: str = "#090502"           # GenLauncherDarkBackGround — near black
    light_bg: str = "rgba(0,0,0,0.70)" # GenLauncherLightBackGround — #B3000000
    text: str = "#ffffff"              # GenLauncherDefaultTextColor — white
    download_text: str = "#090502"     # GenLauncherDownloadTextColor
    selection1: str = "#E61d2057"      # GenLauncherListBoxSelectionColor1
    selection2: str = "#F21d2057"      # GenLauncherListBoxSelectionColor2
    button_selection: str = "#2534ff"  # GenLauncherButtonSelectionColor
    # GenLauncherErrorColor. Rewritten's presets pass "Red"; the per-mod
    # manifest override for it is not parsed yet, so the default always wins.
    error: str = "Red"


DEFAULT_COLORS = Colors()


def colors_from_mod(ci: "ColorsInfo") -> Colors:
    """Build a Colors instance by overlaying a mod's ColorsInfo on defaults."""
    from ..core.models import ColorsInfo  # noqa: F811

    c = Colors()
    if ci.border_color:
        c.border = ci.border_color
    if ci.inactive_border:
        c.inactive_border = ci.inactive_border
    if ci.active_color:
        c.active = ci.active_color
    if ci.dark_fill_color:
        c.dark_fill = ci.dark_fill_color
    if ci.dark_background:
        c.dark_bg = ci.dark_background
    if ci.light_background:
        c.light_bg = ci.light_background
    if ci.default_text_color:
        c.text = ci.default_text_color
    if ci.button_selection_color:
        c.button_selection = ci.button_selection_color
    if ci.listbox_selection_color_1:
        c.selection1 = ci.listbox_selection_color_1
    if ci.listbox_selection_color_2:
        c.selection2 = ci.listbox_selection_color_2
    return c


def generate_qss(c: Colors | None = None) -> str:
    """Build the complete QSS stylesheet for the application."""
    if c is None:
        c = DEFAULT_COLORS
    return _QSS.format(c=c)


# The stylesheet uses {c.field} placeholders resolved by str.format().
# QSS subset of CSS — no variables, no calc(), limited selectors.
_QSS = """\
/* ===== Global ===== */

QWidget {{
    color: {c.text};
    font-family: "Segoe UI", "Noto Sans", sans-serif;
}}

QToolTip {{
    background: {c.dark_bg};
    color: {c.text};
    border: 1px solid {c.border};
    padding: 4px;
}}

/* ===== QPushButton ===== */

QPushButton {{
    background: {c.light_bg};
    border: 2px solid {c.border};
    color: {c.text};
    padding: 1px;
    qproperty-focusPolicy: NoFocus;
}}

QPushButton:hover {{
    background: qlineargradient(
        x1:0, y1:0, x2:1, y2:0,
        stop:0   rgba(0,0,0,0.70),
        stop:0.1 {c.button_selection},
        stop:0.9 {c.button_selection},
        stop:1   rgba(0,0,0,0.70)
    );
    color: {c.active};
}}

QPushButton:pressed {{
    background: {c.light_bg};
}}

QPushButton:disabled {{
    background: {c.dark_bg};
    color: {c.inactive_border};
    border-color: {c.inactive_border};
}}

/* ===== QListWidget ===== */

QListWidget {{
    background: transparent;
    border: none;
    outline: none;
}}

QListWidget::item {{
    border: 2px solid {c.inactive_border};
    padding: 0px;
    margin: 1px 0px;
}}

QListWidget::item:selected {{
    border-color: {c.border};
    background: qlineargradient(
        x1:0, y1:0, x2:1, y2:0,
        stop:0.0 {c.selection1},
        stop:0.7 {c.selection2},
        stop:1.0 transparent
    );
}}

QListWidget::item:hover:!selected {{
    border-color: {c.inactive_border2};
}}

/* ===== QScrollBar (vertical, 18px wide) ===== */

QScrollBar:vertical {{
    background: transparent;
    width: 18px;
    margin: 18px 0 18px 0;
}}

QScrollBar::handle:vertical {{
    background: {c.dark_fill};
    border: 1.5px solid {c.border};
    min-height: 20px;
    margin: 1px;
}}

QScrollBar::handle:vertical:hover {{
    border-color: {c.active};
}}

QScrollBar::add-line:vertical {{
    background: {c.dark_bg};
    border: 1px solid {c.border};
    height: 18px;
    subcontrol-position: bottom;
    subcontrol-origin: margin;
}}

QScrollBar::sub-line:vertical {{
    background: {c.dark_bg};
    border: 1px solid {c.border};
    height: 18px;
    subcontrol-position: top;
    subcontrol-origin: margin;
}}

QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
    background: transparent;
}}

QScrollBar::up-arrow:vertical, QScrollBar::down-arrow:vertical {{
    width: 8px;
    height: 8px;
    /* Qt will draw default arrows; colors come from border */
}}

/* ===== QScrollBar (horizontal, 18px tall) ===== */

QScrollBar:horizontal {{
    background: transparent;
    height: 18px;
    margin: 0 18px 0 18px;
}}

QScrollBar::handle:horizontal {{
    background: {c.dark_fill};
    border: 1.5px solid {c.border};
    min-width: 20px;
    margin: 1px;
}}

QScrollBar::handle:horizontal:hover {{
    border-color: {c.active};
}}

QScrollBar::add-line:horizontal {{
    background: {c.dark_bg};
    border: 1px solid {c.border};
    width: 18px;
    subcontrol-position: right;
    subcontrol-origin: margin;
}}

QScrollBar::sub-line:horizontal {{
    background: {c.dark_bg};
    border: 1px solid {c.border};
    width: 18px;
    subcontrol-position: left;
    subcontrol-origin: margin;
}}

QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{
    background: transparent;
}}

/* ===== QProgressBar ===== */

QProgressBar {{
    background: {c.dark_bg};
    border: 1px solid {c.inactive_border};
    text-align: center;
    color: {c.download_text};
    font-weight: bold;
}}

QProgressBar::chunk {{
    background: {c.active};
}}

/* ===== QComboBox ===== */

QComboBox {{
    background: {c.dark_fill};
    border: 1px solid {c.border};
    color: {c.text};
    padding: 3px 23px 3px 3px;
    min-width: 120px;
    min-height: 20px;
}}

QComboBox:hover {{
    border-color: {c.active};
}}

QComboBox::drop-down {{
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: 20px;
    border-left: 1px solid {c.border};
}}

QComboBox::down-arrow {{
    width: 8px;
    height: 6px;
}}

QComboBox QAbstractItemView {{
    background: {c.dark_fill};
    border: 1px solid {c.border};
    color: {c.text};
    selection-background-color: {c.inactive_border2};
    padding: 4px 6px;
    outline: none;
}}

/* ===== QRadioButton ===== */

QRadioButton {{
    spacing: 6px;
    color: {c.text};
}}

QRadioButton::indicator {{
    width: 18px;
    height: 18px;
    border: 2px solid {c.inactive_border2};
    border-radius: 10px;
    background: {c.dark_bg};
}}

QRadioButton::indicator:checked {{
    background: qradialgradient(
        cx:0.5, cy:0.5, radius:0.4,
        fx:0.5, fy:0.5,
        stop:0 {c.border},
        stop:0.6 {c.border},
        stop:0.7 {c.dark_bg});
    border-color: {c.border};
}}

QRadioButton:disabled {{
    color: {c.inactive_border};
}}

/* ===== QCheckBox (same style as RadioButton) ===== */

QCheckBox {{
    spacing: 6px;
    color: {c.text};
}}

QCheckBox::indicator {{
    width: 18px;
    height: 18px;
    border: 2px solid {c.inactive_border2};
    background: {c.dark_bg};
}}

QCheckBox::indicator:checked {{
    background: {c.border};
    border-color: {c.inactive_border2};
}}

/* ===== QSlider (horizontal) ===== */

QSlider::groove:horizontal {{
    height: 4px;
    background: {c.dark_fill};
    margin: 5px 0;
}}

QSlider::handle:horizontal {{
    width: 11px;
    height: 18px;
    background: {c.border};
    border: 1px solid {c.inactive_border2};
    margin: -7px 0;
}}

QSlider::handle:horizontal:hover {{
    background: {c.active};
    border-color: {c.border};
}}

QSlider::groove:horizontal:disabled {{
    background: {c.inactive_border2};
}}

QSlider::handle:horizontal:disabled {{
    background: {c.inactive_border};
    border-color: {c.inactive_border2};
}}

/* ===== QSlider (vertical) ===== */

QSlider::groove:vertical {{
    width: 4px;
    background: {c.dark_fill};
    margin: 0 5px;
}}

QSlider::handle:vertical {{
    width: 18px;
    height: 11px;
    background: {c.border};
    border: 1px solid {c.inactive_border2};
    margin: 0 -7px;
}}

QSlider::handle:vertical:hover {{
    background: {c.active};
    border-color: {c.border};
}}

/* ===== QMenu / Context menu ===== */

QMenu {{
    background: {c.dark_bg};
    border: 1px solid {c.border};
    color: {c.text};
    padding: 2px;
}}

QMenu::item {{
    padding: 4px 10px;
    height: 24px;
    min-width: 200px;
}}

QMenu::item:selected {{
    background: {c.button_selection};
}}

QMenu::separator {{
    height: 1px;
    background: {c.inactive_border};
    margin: 2px 5px;
}}

/* ===== QLineEdit / QTextEdit ===== */

QLineEdit, QTextEdit {{
    background: {c.dark_bg};
    border: 2px solid {c.border};
    color: {c.text};
    padding: 2px 4px;
    selection-background-color: {c.button_selection};
}}

QLineEdit:focus, QTextEdit:focus {{
    border-color: {c.active};
}}

/* ===== QLabel ===== */

QLabel {{
    background: transparent;
    border: none;
}}

/* ===== QTabBar (not used, but just in case) ===== */

QTabBar::tab {{
    background: {c.light_bg};
    border: 2px solid {c.border};
    color: {c.text};
    padding: 4px 12px;
}}

QTabBar::tab:selected {{
    background: {c.button_selection};
    color: {c.active};
}}

QTabBar::tab:hover:!selected {{
    color: {c.active};
}}

/* ===== Panel frames (left mod list + right button panel) ===== */

QFrame#leftPanel, QFrame#rightPanel {{
    border: 2px solid {c.border};
    background: transparent;
}}
"""
