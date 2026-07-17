"""The break-prompt overlay window.

Frameless, always-on-top, hosts the animated exercise SVG (rendered by
QSvgWidget so SMIL animations play natively — no cairosvg roundtrip).
Countdown ring below, Skip and Done buttons, one-line instruction.

The window blocks nothing and steals no focus (WA_ShowWithoutActivating);
the user can keep typing while glancing at the pet's demo.
"""

from __future__ import annotations

import os
from typing import Callable

from PySide6.QtCore import Qt, QTimer, QRectF
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtCore import QByteArray
from PySide6.QtSvgWidgets import QSvgWidget
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from . import exercises
from .svg_inline import load_inlined


class _CountdownRing(QWidget):
    """A small circular countdown that empties over `total_s`."""

    def __init__(self, total_s: int, parent=None):
        super().__init__(parent)
        self.total_s = max(1, total_s)
        self.remaining = float(total_s)
        self.setFixedSize(42, 42)

    def set_remaining(self, seconds: float) -> None:
        self.remaining = max(0.0, seconds)
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(4, 4, self.width() - 8, self.height() - 8)
        # Track (background)
        pen = QPen(QColor("#334155"))
        pen.setWidthF(3.5)
        p.setPen(pen)
        p.drawArc(rect, 0, 360 * 16)
        # Active arc (blue → warm as time runs out)
        pct = self.remaining / self.total_s
        color = QColor("#3FA3FF") if pct > 0.3 else QColor("#F97316")
        pen.setColor(color)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        p.drawArc(rect, 90 * 16, int(-360 * 16 * (1 - pct)))
        # Number
        p.setPen(QColor("white"))
        f = p.font(); f.setBold(True); f.setPointSize(10); p.setFont(f)
        p.drawText(self.rect(), Qt.AlignCenter, f"{int(self.remaining)}s")


class BreakOverlay(QDialog):
    """Frameless break-prompt panel. Not modal — user can dismiss by clicking
    Skip or Done; auto-completes when the countdown hits zero."""

    def __init__(self, exercise_slug: str, on_close: Callable[[bool], None]):
        super().__init__(None)
        self.exercise = exercises.get(exercise_slug)
        if self.exercise is None:
            # Bail cleanly — never crash the pet over a missing catalog entry.
            self._done_callback = on_close
            self.close()
            return

        self._done_callback = on_close
        self._decided = False           # Skip/Done only fires once

        self.setWindowTitle(f"Claude Pet — {self.exercise.name}")
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
            | Qt.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        # Solid card look — the animated SVG is transparent so we need a bg.
        self.setStyleSheet(
            "QDialog { background: #0A0A1F; border: 1px solid #334155; "
            "border-radius: 14px; } "
            "QLabel { color: white; } "
            "QPushButton { background: #1F2937; color: white; border: none; "
            "padding: 8px 18px; border-radius: 8px; font-weight: 600; } "
            "QPushButton#done { background: #10B981; } "
            "QPushButton:hover { background: #374151; } "
            "QPushButton#done:hover { background: #34D399; }"
        )
        self.setFixedSize(280, 380)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(8)

        title = QLabel(self.exercise.name)
        title.setStyleSheet("font-size: 15px; font-weight: 700;")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        # The animated SVG — QSvgWidget honors SMIL natively. We inline the
        # root's CSS custom properties first because Qt's SVG renderer
        # ignores `var(--name)`; without this, every accent element (monitor
        # icons, water color, "20 ft" labels, arrow guides, countdown ring)
        # falls back to no-fill and disappears.
        self.svg = QSvgWidget()
        self.svg.load(QByteArray(load_inlined(self.exercise.svg_path())))
        self.svg.setFixedSize(240, 240)
        layout.addWidget(self.svg, alignment=Qt.AlignCenter)

        instr = QLabel(self.exercise.instruction)
        instr.setStyleSheet("color: #B8B8B8; font-size: 11px;")
        instr.setWordWrap(True)
        instr.setAlignment(Qt.AlignCenter)
        layout.addWidget(instr)

        bottom = QHBoxLayout()
        self.ring = _CountdownRing(self.exercise.duration_s)
        bottom.addWidget(self.ring)
        bottom.addStretch(1)

        self.skip_btn = QPushButton("Skip")
        self.skip_btn.clicked.connect(lambda: self._finish(completed=False))
        bottom.addWidget(self.skip_btn)

        self.done_btn = QPushButton("Done")
        self.done_btn.setObjectName("done")
        self.done_btn.clicked.connect(lambda: self._finish(completed=True))
        bottom.addWidget(self.done_btn)
        layout.addLayout(bottom)

        # Countdown timer — every 200 ms is smooth and cheap.
        self._remaining = float(self.exercise.duration_s)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(200)

        # Bottom-right, above the pet if possible.
        self._place_near_pet()

    def _place_near_pet(self):
        from PySide6.QtGui import QGuiApplication
        screen = QGuiApplication.primaryScreen().availableGeometry()
        margin = 24
        # Right side, vertically centered — visible but not covering the pet
        # (which lives in the bottom-right corner).
        x = screen.right() - self.width() - margin
        y = screen.top() + (screen.height() - self.height()) // 2
        self.move(x, y)

    def _tick(self):
        self._remaining -= 0.2
        self.ring.set_remaining(self._remaining)
        if self._remaining <= 0:
            self._finish(completed=True)

    def _finish(self, completed: bool):
        if self._decided:
            return
        self._decided = True
        try:
            self._timer.stop()
        except Exception:
            pass
        try:
            self._done_callback(completed)
        except Exception:
            pass
        self.close()
