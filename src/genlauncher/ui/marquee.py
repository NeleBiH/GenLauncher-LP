"""Clickable status-bar text that scrolls from right to left."""

from __future__ import annotations

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPainter
from PySide6.QtWidgets import QSizePolicy, QWidget

from .theme import ui_font


class MarqueeLabel(QWidget):
    clicked = Signal()

    def __init__(self, color: str = "#ffcc00", speed: int = 2, interval_ms: int = 30, parent=None):
        super().__init__(parent)
        self._text = ""
        self._color = QColor(color)
        self._speed = speed
        self._offset = 0
        self._timer = QTimer(self)
        self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(self._step)
        self.setFont(ui_font(10, bold=True))
        self.setFixedHeight(20)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.hide()

    def start(self, text: str) -> None:
        self._text = text
        self._offset = self.width()
        self.setToolTip(text)
        self.show()
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()
        self.hide()

    def _step(self) -> None:
        self._offset -= self._speed
        if self._offset < -QFontMetrics(self.font()).horizontalAdvance(self._text):
            self._offset = self.width()
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setFont(self.font())
        painter.setPen(self._color)
        # A rect-based drawText would clip text wider than the widget.
        metrics = QFontMetrics(self.font())
        baseline = (self.height() + metrics.ascent() - metrics.descent()) // 2
        painter.drawText(self._offset, baseline, self._text)
        painter.end()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
