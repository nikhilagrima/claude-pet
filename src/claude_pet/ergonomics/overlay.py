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


class _AutoCloseBar(QWidget):
    """Thin horizontal progress bar at the top of the overlay — clearly the
    'auto-close in Ns' meter, so it never competes with any countdown ring
    baked into the exercise SVG itself."""

    def __init__(self, total_s: int, parent=None):
        super().__init__(parent)
        self.total_s = max(1, total_s)
        self.remaining = float(total_s)
        self.setFixedHeight(4)

    def set_remaining(self, seconds: float) -> None:
        self.remaining = max(0.0, seconds)
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        # Track
        p.fillRect(self.rect(), QColor("#1B3A70"))
        # Filled portion — cyan, cools to orange when <30% left
        pct = self.remaining / self.total_s
        w = int(self.width() * pct)
        color = QColor("#4FC3F7") if pct > 0.3 else QColor("#F97316")
        p.fillRect(0, 0, w, self.height(), color)


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
        # Solid card — the SVG's own background is transparent.
        self.setStyleSheet(
            "QDialog { background: #061027; border: 1px solid #1B3A70; "
            "border-radius: 14px; } "
            "QLabel { color: #DDEBFF; background: transparent; "
            "font-family: 'JetBrains Mono', Menlo, monospace; } "
            "QPushButton { background: #0F2140; color: #DDEBFF; "
            "border: 1px solid #1B3A70; padding: 8px 20px; border-radius: 8px; "
            "font-weight: 600; font-family: 'JetBrains Mono', Menlo, monospace; "
            "letter-spacing: 0.5px; min-height: 22px; } "
            "QPushButton:hover { border-color: #4FC3F7; color: #4FC3F7; } "
            "QPushButton#done { border-color: #4ADE80; color: #4ADE80; } "
            "QPushButton#done:hover { background: #14532D; }"
        )
        # No title/instruction labels — the SVG has its own title pill and
        # instruction text baked in. Sizing = SVG + auto-close bar + buttons.
        self.setFixedSize(288, 320)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)

        # Thin auto-close bar at the top — clearly the overlay's timer,
        # visually distinct from any countdown ring baked into the SVG below.
        self.ring = _AutoCloseBar(self.exercise.duration_s)
        layout.addWidget(self.ring)

        # The animated SVG — QSvgWidget honors SMIL natively.
        # Inline CSS custom properties first (Qt doesn't process var(--…)).
        self.svg = QSvgWidget()
        self.svg.load(QByteArray(load_inlined(self.exercise.svg_path())))
        self.svg.setFixedSize(260, 232)   # ratio matches SVG viewBox
        layout.addWidget(self.svg, alignment=Qt.AlignCenter)

        bottom = QHBoxLayout()
        bottom.addStretch(1)
        self.skip_btn = QPushButton("Skip")
        self.skip_btn.setCursor(Qt.PointingHandCursor)
        self.skip_btn.clicked.connect(lambda: self._finish(completed=False))
        bottom.addWidget(self.skip_btn)

        self.done_btn = QPushButton("Done")
        self.done_btn.setObjectName("done")
        self.done_btn.setCursor(Qt.PointingHandCursor)
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
