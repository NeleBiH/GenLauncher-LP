"""InfoWindow — the launcher's own message dialog.

Mirrors Rewritten's ``InfoWindow`` + ``InfoDialogViewModel``: a titled band on
top, a framed body carrying a kind badge next to the detail text, and a footer
whose buttons appear according to the dialog kind. ``QMessageBox`` has none of
that, so long error text used to be squeezed into a single line with no place
to put a traceback or a file list.

The four kinds and their button visibility follow the reference exactly::

    Info                 OK
    Error                OK
    WarningConfirmation  Continue (default) + Cancel
    InfoAction           Action   + Cancel

Closing the window with X counts as "no" for the two kinds that ask a
question, and as OK for the two that do not.
"""

from __future__ import annotations

from enum import Enum

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .theme import DEFAULT_COLORS, Colors, ui_font

HEADER_HEIGHT = 56
FOOTER_HEIGHT = 68
BODY_MAX_HEIGHT = 330
BADGE_SIZE = 52


class DialogKind(str, Enum):
    """Which shape of message this is, as in ``InfoDialogKind``."""

    INFO = "info"
    INFO_ACTION = "info_action"
    ERROR = "error"
    WARNING_CONFIRMATION = "warning_confirmation"
    SUCCESS = "success"


def _octagon_path() -> QPainterPath:
    """The reference's error outline: a square with clipped corners."""
    corners = (
        (16, 5), (36, 5), (47, 16), (47, 36),
        (36, 47), (16, 47), (5, 36), (5, 16),
    )
    path = QPainterPath()
    path.moveTo(*corners[0])
    for corner in corners[1:]:
        path.lineTo(*corner)
    path.closeSubpath()
    return path


class KindBadge(QWidget):
    """52×52 badge drawing the info / warning / error glyph.

    Geometry is taken from Rewritten's inline shapes so the three badges
    line up consistently.
    """

    def __init__(self, kind: DialogKind, colors: Colors = DEFAULT_COLORS, parent=None):
        super().__init__(parent)
        self._kind = kind
        self._colors = colors
        self.setFixedSize(BADGE_SIZE, BADGE_SIZE)

    @property
    def _accent(self) -> str:
        if self._kind is DialogKind.ERROR:
            return self._colors.error
        if self._kind is DialogKind.WARNING_CONFIRMATION:
            return self._colors.active
        if self._kind is DialogKind.SUCCESS:
            return "#4CAF50"
        return self._colors.border

    def paintEvent(self, event) -> None:  # noqa: D102 - Qt template method
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        accent = QColor(self._accent)
        pen = QPen(accent, 2)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(QColor(self._colors.dark_bg))
        painter.drawRect(self.rect().adjusted(1, 1, -1, -1))

        painter.setBrush(accent)

        dark = QColor(self._colors.dark_bg)

        if self._kind is DialogKind.SUCCESS:
            painter.drawEllipse(QRectF(9, 9, 34, 34))
            check_pen = QPen(dark, 3.5)
            check_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            check_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(check_pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPolyline(
                QPolygonF([QPointF(17, 27), QPointF(23, 33), QPointF(35, 19)])
            )
        elif self._kind is DialogKind.ERROR:
            painter.drawPath(_octagon_path())
            painter.setBrush(dark)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(QRectF(23.5, 10, 5, 19), 2, 2)
            painter.drawEllipse(QRectF(23, 33, 6, 6))
        elif self._kind is DialogKind.WARNING_CONFIRMATION:
            painter.drawPolygon(
                QPolygonF([QPointF(26, 6), QPointF(47, 44), QPointF(5, 44)])
            )
            painter.setBrush(dark)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(QRectF(24, 16, 4, 16), 2, 2)
            painter.drawEllipse(QRectF(23.5, 36, 5, 5))
        else:
            painter.drawEllipse(QRectF(9, 9, 34, 34))
            painter.setBrush(dark)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(QRectF(23.5, 11, 5, 5))
            painter.drawRoundedRect(QRectF(23.5, 27, 5, 15), 2, 2)

        painter.end()

    def sizeHint(self) -> QSize:
        return QSize(BADGE_SIZE, BADGE_SIZE)


class InfoDialog(QDialog):
    """A titled, framed message box with a kind badge."""

    def __init__(
        self,
        main: str,
        detail: str = "",
        kind: DialogKind = DialogKind.INFO,
        *,
        badge: DialogKind | None = None,
        continue_text: str = "Continue",
        cancel_text: str = "Cancel",
        action_text: str = "",
        detail_font_size: int = 11,
        default_continue: bool = True,
        parent=None,
        colors: Colors = DEFAULT_COLORS,
    ):
        super().__init__(parent)
        self._kind = kind
        self._badge = badge or kind
        self._main = main
        self._detail = detail
        self._continue_text = continue_text
        self._cancel_text = cancel_text
        self._action_text = action_text
        self._detail_font_size = detail_font_size
        self._default_continue = default_continue
        self._colors = colors

        self.setWindowTitle(main)
        self.setMinimumWidth(480)
        self.setMaximumHeight(520)
        self.setWindowFlags(
            Qt.WindowType.Dialog | Qt.WindowType.WindowCloseButtonHint
        )
        self._build_ui(detail_font_size)

    # ---- visibility rules, as in InfoDialogViewModel ------------------------

    @property
    def _ok_visible(self) -> bool:
        return self._kind in (DialogKind.INFO, DialogKind.ERROR, DialogKind.SUCCESS)

    @property
    def _action_visible(self) -> bool:
        return self._kind is DialogKind.INFO_ACTION and bool(self._action_text)

    @property
    def _continue_visible(self) -> bool:
        return self._kind is DialogKind.WARNING_CONFIRMATION

    @property
    def _cancel_visible(self) -> bool:
        return self._kind in (
            DialogKind.WARNING_CONFIRMATION,
            DialogKind.INFO_ACTION,
        )

    @property
    def _icon_kind(self) -> DialogKind:
        """Which glyph to draw.

        ``badge`` lets a plain (single-OK) message carry the warning mark that
        QMessageBox would draw for it.
        """
        if self._badge in (DialogKind.ERROR, DialogKind.WARNING_CONFIRMATION, DialogKind.SUCCESS):
            return self._badge
        return DialogKind.INFO

    # ---- construction -------------------------------------------------------

    def _build_ui(self, detail_font_size: int) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QLabel(self._main, self)
        header.setFixedHeight(HEADER_HEIGHT)
        header.setFont(ui_font(13, bold=True))
        header.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        header.setStyleSheet(
            f"background: {self._colors.dark_fill};"
            f" color: {self._colors.text};"
            f" padding-left: 21px; padding-right: 21px;"
        )
        layout.addWidget(header)

        body = QScrollArea(self)
        body.setWidgetResizable(True)
        body.setFrameShape(QFrame.Shape.NoFrame)
        body.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body.setMaximumHeight(BODY_MAX_HEIGHT + 40)
        body.setStyleSheet(f"background: {self._colors.dark_bg};")

        inner = QWidget(body)
        inner_layout = QVBoxLayout(inner)
        inner_layout.setContentsMargins(21, 20, 21, 20)

        frame = QWidget(inner)
        frame.setStyleSheet(
            f"background: {self._colors.light_bg};"
            f"border: 1px solid {self._colors.dark_fill};"
        )
        frame_layout = QVBoxLayout(frame)
        frame_layout.setContentsMargins(20, 18, 20, 18)
        frame_layout.setSpacing(20)

        frame_layout.addWidget(
            KindBadge(self._icon_kind, self._colors, frame),
            0,
            Qt.AlignmentFlag.AlignCenter,
        )

        if self._detail:
            text = QLabel(self._detail, frame)
            text.setWordWrap(True)
            text.setFont(ui_font(detail_font_size))
            text.setStyleSheet(f"color: {self._colors.text};")
            frame_layout.addWidget(text)

        inner_layout.addWidget(frame)
        body.setWidget(inner)
        layout.addWidget(body, 1)

        footer = QWidget(self)
        footer.setFixedHeight(FOOTER_HEIGHT)
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(21, 0, 21, 0)
        footer_layout.addStretch(1)

        cancel_button = continue_button = None
        if self._cancel_visible:
            cancel_button = self._make_button(self._cancel_text, accept=False)
            footer_layout.addWidget(cancel_button)
        if self._continue_visible:
            continue_button = self._make_button(
                self._continue_text, accept=True, primary=True
            )
            footer_layout.addWidget(continue_button)
        if self._action_visible:
            footer_layout.addWidget(
                self._make_button(self._action_text, accept=True, primary=True)
            )
        if self._ok_visible:
            footer_layout.addWidget(self._make_button("OK", accept=True, primary=True))

        # QMessageBox took an explicit default button; a destructive question
        # must not have Enter mean "yes".
        if cancel_button is not None and continue_button is not None:
            if self._default_continue:
                cancel_button.setAutoDefault(False)
            else:
                continue_button.setAutoDefault(False)
                cancel_button.setDefault(True)

        layout.addWidget(footer)

    def _make_button(self, text: str, *, accept: bool, primary: bool = False) -> QPushButton:
        button = QPushButton(text, self)
        button.setFont(ui_font(11))
        if primary:
            button.setDefault(True)
        button.clicked.connect(self.accept if accept else self.reject)
        return button

    # ---- QMessageBox-shaped helpers ----------------------------------------
    #
    # QDialog already maps the window's X button to reject(), which is the
    # answer a question wants. Overriding closeEvent to make X mean "OK" for
    # plain messages would re-enter it from accept()/reject(), so the message
    # kinds simply do not care about the return value.

    @classmethod
    def information(cls, parent, title: str, text: str = "", *_args, **_kwargs) -> None:
        """Show an informational message. ``QMessageBox.information`` shape."""
        cls(title, text, DialogKind.INFO, parent=parent).exec()

    @classmethod
    def success(cls, parent, title: str, text: str = "", *_args, **_kwargs) -> None:
        """Show a success message with a green checkmark badge."""
        cls(title, text, DialogKind.SUCCESS, parent=parent).exec()

    @classmethod
    def warning(cls, parent, title: str, text: str = "", *_args, **_kwargs) -> None:
        """Show a warning. ``QMessageBox.warning`` shape.

        Still a single-OK message, but drawn with the warning glyph.
        """
        cls(
            title,
            text,
            DialogKind.INFO,
            badge=DialogKind.WARNING_CONFIRMATION,
            parent=parent,
        ).exec()

    @classmethod
    def critical(cls, parent, title: str, text: str = "", *_args, **_kwargs) -> None:
        """Show an error. ``QMessageBox.critical`` shape."""
        cls(title, text, DialogKind.ERROR, parent=parent).exec()

    @classmethod
    def question(
        cls,
        parent,
        title: str,
        text: str = "",
        *_args,
        default_button: bool = True,
        continue_text: str = "Continue",
        cancel_text: str = "Cancel",
    ) -> bool:
        """Ask a yes/no question. ``QMessageBox.question`` shape.

        Returns True only when Continue is pressed; the X button means no.
        ``default_button=False`` keeps Enter from answering yes.
        """
        dialog = cls(
            title,
            text,
            DialogKind.WARNING_CONFIRMATION,
            continue_text=continue_text,
            cancel_text=cancel_text,
            default_continue=default_button,
            parent=parent,
        )
        return dialog.exec() == int(QDialog.DialogCode.Accepted)
