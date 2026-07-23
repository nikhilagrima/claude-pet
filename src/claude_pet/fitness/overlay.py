"""Small fitness bubbles — workout nudge, weigh-in, meal check.

MONOCHROME palette only (per spec):
  backgrounds  #000000 / #0A0A0A
  primary text #FFFFFF
  greys        #A0A0A0 / #B8B8B8
  borders      #1F1F1F
No cyan, no purple, no other hues. Transparent SVG-safe backgrounds where
we render SVG (none right now — text-only bubbles keep this simple).

Same Qt window-flag pattern the ergonomics BreakOverlay uses so macOS
always-on-top behaves consistently.
"""

from __future__ import annotations

import sys
from typing import Callable, Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QDialog, QDoubleSpinBox, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QVBoxLayout, QWidget,
)


# Monochrome palette — LOCKED. Any UI added under fitness/ must use only these.
MONO = {
    "bg":         "#000000",
    "bg_soft":    "#0A0A0A",
    "text":       "#FFFFFF",
    "text_dim":   "#B8B8B8",
    "text_muted": "#A0A0A0",
    "border":     "#1F1F1F",
}


_STYLE = f"""
QDialog {{
    background: {MONO['bg']};
    border: 1px solid {MONO['border']};
    border-radius: 12px;
}}
QLabel {{
    color: {MONO['text']};
    background: transparent;
    font-family: 'Inter', -apple-system, 'SF Pro Text', sans-serif;
}}
QLabel[muted="true"] {{
    color: {MONO['text_dim']};
    font-size: 11px;
}}
QPushButton {{
    background: {MONO['bg_soft']};
    color: {MONO['text']};
    border: 1px solid {MONO['border']};
    padding: 8px 16px;
    border-radius: 6px;
    font-weight: 500;
    font-family: 'Inter', sans-serif;
    min-height: 22px;
}}
QPushButton:hover {{
    background: #141414;
    border: 1px solid {MONO['text_muted']};
}}
QPushButton#primary {{
    background: {MONO['text']};
    color: {MONO['bg']};
    border: 1px solid {MONO['text']};
    font-weight: 600;
}}
QPushButton#primary:hover {{
    background: {MONO['text_dim']};
}}
QLineEdit, QDoubleSpinBox {{
    background: {MONO['bg_soft']};
    color: {MONO['text']};
    border: 1px solid {MONO['border']};
    border-radius: 6px;
    padding: 7px 10px;
    font-family: 'Inter', sans-serif;
    min-height: 20px;
}}
QLineEdit:focus, QDoubleSpinBox:focus {{
    border: 1px solid {MONO['text_dim']};
}}
"""


def _place_near_pet(dialog: QDialog) -> None:
    """Right side, vertically centered — same convention as the ergonomics
    break overlay."""
    screen = QGuiApplication.primaryScreen().availableGeometry()
    margin = 24
    x = screen.right() - dialog.width() - margin
    y = screen.top() + (screen.height() - dialog.height()) // 2
    dialog.move(x, y)


def _base_dialog(width: int, height: int, title: str) -> QDialog:
    d = QDialog(None)
    d.setWindowTitle(title)
    d.setWindowFlags(
        Qt.FramelessWindowHint
        | Qt.WindowStaysOnTopHint
        | Qt.NoDropShadowWindowHint
    )
    d.setAttribute(Qt.WA_ShowWithoutActivating, True)
    d.setFixedSize(width, height)
    d.setStyleSheet(_STYLE)
    return d


# --- WORKOUT NUDGE ----------------------------------------------------------

class WorkoutBubble(QDialog):
    """Read-only display of today's plan + kcal/protein/step targets."""

    def __init__(self, nudge_text: str, on_close: Callable[[], None]):
        super().__init__(None)
        self._done_callback = on_close
        self.setWindowTitle("Claude Pet — Today's workout")
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setFixedSize(340, 260)
        self.setStyleSheet(_STYLE)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(8)

        header = QLabel("Workout nudge")
        header.setProperty("muted", "true")
        layout.addWidget(header)

        body = QLabel(nudge_text)
        body.setWordWrap(True)
        layout.addWidget(body)

        layout.addStretch(1)

        row = QHBoxLayout()
        row.addStretch(1)
        close_btn = QPushButton("Got it")
        close_btn.setObjectName("primary")
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.clicked.connect(self._finish)
        row.addWidget(close_btn)
        layout.addLayout(row)

        _place_near_pet(self)

    def _finish(self):
        try:
            self._done_callback()
        except Exception:
            pass
        self.hide()
        self.deleteLater()


# --- WEIGH-IN INPUT ---------------------------------------------------------

class WeighInBubble(QDialog):
    """Simple numeric spinbox to log today's weight in kg."""

    def __init__(self, current_kg: float,
                 on_submit: Callable[[float], None],
                 on_dismiss: Callable[[], None]):
        super().__init__(None)
        self._on_submit = on_submit
        self._on_dismiss = on_dismiss
        self.setWindowTitle("Claude Pet — Weigh-in")
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setFixedSize(300, 160)
        self.setStyleSheet(_STYLE)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(10)

        h = QLabel("Weigh-in")
        h.setProperty("muted", "true")
        layout.addWidget(h)
        layout.addWidget(QLabel("Today's weight (kg):"))

        self.spin = QDoubleSpinBox()
        self.spin.setDecimals(1)
        self.spin.setRange(30.0, 300.0)
        self.spin.setSingleStep(0.1)
        self.spin.setValue(float(current_kg))
        self.spin.setSuffix(" kg")
        layout.addWidget(self.spin)

        row = QHBoxLayout()
        row.addStretch(1)
        skip = QPushButton("Skip")
        skip.setCursor(Qt.PointingHandCursor)
        skip.clicked.connect(self._skip)
        row.addWidget(skip)
        log = QPushButton("Log")
        log.setObjectName("primary")
        log.setCursor(Qt.PointingHandCursor)
        log.clicked.connect(self._log)
        row.addWidget(log)
        layout.addLayout(row)

        _place_near_pet(self)

    def _log(self):
        val = float(self.spin.value())
        try:
            self._on_submit(val)
        except Exception:
            pass
        self.hide(); self.deleteLater()

    def _skip(self):
        try:
            self._on_dismiss()
        except Exception:
            pass
        self.hide(); self.deleteLater()


# --- MEAL CHECK -------------------------------------------------------------

class MealCheckBubble(QDialog):
    """Yes/no on whether the day was on plan; optional short note."""

    def __init__(self,
                 on_submit: Callable[[bool, str], None],
                 on_dismiss: Callable[[], None]):
        super().__init__(None)
        self._on_submit = on_submit
        self._on_dismiss = on_dismiss
        self.setWindowTitle("Claude Pet — Meal check")
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setFixedSize(320, 200)
        self.setStyleSheet(_STYLE)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(10)

        h = QLabel("Meal check")
        h.setProperty("muted", "true")
        layout.addWidget(h)
        layout.addWidget(QLabel("Were meals on plan today?"))

        self.note = QLineEdit()
        self.note.setPlaceholderText("optional note")
        layout.addWidget(self.note)

        row = QHBoxLayout()
        no = QPushButton("Off plan")
        no.setCursor(Qt.PointingHandCursor)
        no.clicked.connect(lambda: self._submit(False))
        row.addWidget(no)
        row.addStretch(1)
        skip = QPushButton("Skip")
        skip.setCursor(Qt.PointingHandCursor)
        skip.clicked.connect(self._skip)
        row.addWidget(skip)
        yes = QPushButton("On plan")
        yes.setObjectName("primary")
        yes.setCursor(Qt.PointingHandCursor)
        yes.clicked.connect(lambda: self._submit(True))
        row.addWidget(yes)
        layout.addLayout(row)

        _place_near_pet(self)

    def _submit(self, on_plan: bool):
        try:
            self._on_submit(on_plan, self.note.text().strip())
        except Exception:
            pass
        self.hide(); self.deleteLater()

    def _skip(self):
        try:
            self._on_dismiss()
        except Exception:
            pass
        self.hide(); self.deleteLater()


# --- COACH NOTE BUBBLE (weekly adjustment from Claude Code) -----------------

class CoachNoteBubble(QDialog):
    """Displays fitness_note.txt content — shown once per new note."""

    def __init__(self, note: str, on_close: Callable[[], None]):
        super().__init__(None)
        self._on_close = on_close
        self.setWindowTitle("Claude Pet — Weekly coaching note")
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setFixedSize(360, 220)
        self.setStyleSheet(_STYLE)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(10)

        h = QLabel("Weekly coaching note")
        h.setProperty("muted", "true")
        layout.addWidget(h)

        body = QLabel(note[:600])
        body.setWordWrap(True)
        layout.addWidget(body)

        layout.addStretch(1)
        row = QHBoxLayout()
        row.addStretch(1)
        close = QPushButton("Got it")
        close.setObjectName("primary")
        close.setCursor(Qt.PointingHandCursor)
        close.clicked.connect(self._finish)
        row.addWidget(close)
        layout.addLayout(row)

        _place_near_pet(self)

    def _finish(self):
        try:
            self._on_close()
        except Exception:
            pass
        self.hide(); self.deleteLater()
