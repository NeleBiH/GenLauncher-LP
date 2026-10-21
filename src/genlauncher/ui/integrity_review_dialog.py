"""IntegrityReviewDialog — show exactly which files failed verification.

Mirrors Rewritten's ``IntegrityReviewDialog``. The check already collects the
paths; the launcher used to throw them away behind a one-line
"Some mod files have changed: N missing, N corrupted" message box, which
leaves the user with no way to tell a damaged download from a file they edited
themselves.
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

from ..core.integrity import IntegrityResult
from .theme import DEFAULT_COLORS, ui_font

GROUP_ORDER = (
    ("Missing", "missing"),
    ("Corrupted", "corrupted"),
    ("Unexpected", "extra"),
)


def group_issues(result: IntegrityResult) -> list[tuple[str, list[str]]]:
    """Non-empty ``(label, paths)`` groups in display order.

    ``IntegrityResult`` carries three flat lists; the dialog needs them labelled
    so the user can see at a glance what kind of failure they are looking at.
    """
    groups: list[tuple[str, list[str]]] = []
    for label, attr in GROUP_ORDER:
        paths = getattr(result, attr) or []
        if paths:
            groups.append((label, list(paths)))
    return groups


class IntegrityReviewDialog(QDialog):
    """List the failing files and ask whether to launch anyway."""

    def __init__(self, result: IntegrityResult, parent=None):
        super().__init__(parent)
        self._result = result

        self.setWindowTitle("File Integrity")
        self.setFixedSize(560, 420)
        self.setWindowFlags(
            Qt.WindowType.Dialog | Qt.WindowType.WindowCloseButtonHint
        )
        self._build_ui()

    def _build_ui(self) -> None:
        title = QLabel("Some mod files have changed", self)
        title.setGeometry(20, 15, 520, 28)
        title.setFont(ui_font(13, bold=True))
        title.setStyleSheet(f"color: {DEFAULT_COLORS.text};")

        description = QLabel(self._result.summary, self)
        description.setGeometry(20, 45, 520, 24)
        description.setFont(ui_font(10))
        description.setStyleSheet(f"color: {DEFAULT_COLORS.text};")

        self._list = QListWidget(self)
        self._list.setGeometry(20, 78, 520, 268)
        self._list.setFont(ui_font(9))

        for label, paths in group_issues(self._result):
            header = QListWidgetItem(f"{label} ({len(paths)})")
            header.setFlags(Qt.ItemFlag.NoItemFlags)
            header.setData(Qt.ItemDataRole.UserRole, None)
            self._list.addItem(header)
            for path in paths:
                item = QListWidgetItem(f"    {path}")
                item.setData(Qt.ItemDataRole.UserRole, path)
                self._list.addItem(item)

        if self._result.no_baseline:
            note = QLabel(
                "No baseline was recorded, so nothing was actually verified.",
                self,
            )
            note.setGeometry(20, 352, 520, 20)
            note.setFont(ui_font(9))
            note.setStyleSheet(f"color: {DEFAULT_COLORS.text};")

        cancel_btn = QPushButton("Cancel", self)
        cancel_btn.setGeometry(200, 372, 160, 35)
        cancel_btn.setFont(ui_font(11))
        cancel_btn.clicked.connect(self.reject)

        launch_btn = QPushButton("Launch anyway", self)
        launch_btn.setGeometry(370, 372, 170, 35)
        launch_btn.setFont(ui_font(11))
        launch_btn.setDefault(True)
        launch_btn.clicked.connect(self.accept)
