"""Reminder bubble — shown when a stage fires (day-before / 5-min / on-time).

Inherits the Linear-themed panel palette via _STYLE. Buttons are
non-default (Enter must NOT dismiss). Snooze buttons offer 5m/1h/1d;
Done marks completed; Dismiss just closes without marking (the stage is
still recorded as fired so it won't re-pop this tick).
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout,
)


# Reuse fitness overlay palette so this feels consistent — the Reminders
# UI is text-heavy and doesn't need a separate design language.
_MONO = {
    "bg":         "#0A0A0F",
    "bg_soft":    "#141419",
    "text":       "#FFFFFF",
    "text_dim":   "#B4BABF",
    "text_muted": "#6E7079",
    "border":     "#26262E",
    "accent":     "#5E6AD2",
}


_STYLE = f"""
QDialog {{
    background: {_MONO['bg']};
    border: 1px solid {_MONO['border']};
    border-radius: 12px;
}}
QLabel {{
    color: {_MONO['text']};
    background: transparent;
    font-family: 'Inter', -apple-system, sans-serif;
}}
QLabel#header {{
    color: {_MONO['accent']};
    font-family: 'JetBrains Mono', monospace;
    font-size: 10px;
    letter-spacing: 1.5px;
    text-transform: uppercase;
}}
QLabel#muted {{
    color: {_MONO['text_muted']};
    font-size: 11px;
}}
QPushButton {{
    background: {_MONO['bg_soft']};
    color: {_MONO['text']};
    border: 1px solid {_MONO['border']};
    padding: 6px 12px;
    border-radius: 6px;
    font-weight: 500;
    font-family: 'Inter', sans-serif;
    min-height: 22px;
}}
QPushButton:hover {{
    background: #1C1C22;
    border: 1px solid {_MONO['text_muted']};
}}
QPushButton#primary {{
    background: {_MONO['accent']};
    border: 1px solid {_MONO['accent']};
    color: #FFFFFF;
    font-weight: 600;
}}
QPushButton#primary:hover {{
    background: #7A7EEA;
    border-color: #7A7EEA;
}}
"""


def _place_top_right(dialog: QDialog) -> None:
    """Top-right, like the GitHub toast — so it doesn't cover the pet
    (bottom-right) or a fitness bubble (right-middle)."""
    screen = QGuiApplication.primaryScreen().availableGeometry()
    margin = 20
    x = screen.right() - dialog.width() - margin
    y = screen.top() + margin + 60      # below any GitHub toast that might be present
    dialog.move(x, y)


class ReminderBubble(QDialog):
    """One reminder + one stage → one bubble."""

    def __init__(
        self,
        reminder: dict,
        stage_label: str,
        on_done: Callable[[], None],
        on_snooze: Callable[[int], None],   # minutes
        on_dismiss: Callable[[], None],
    ):
        super().__init__(None)
        self._reminder = reminder
        self._on_done = on_done
        self._on_snooze = on_snooze
        self._on_dismiss = on_dismiss

        self.setWindowTitle(f"Claude Pet — Reminder ({stage_label})")
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setFixedSize(380, 200)
        self.setStyleSheet(_STYLE)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(6)

        header = QLabel(f"◆ REMINDER · {stage_label}")
        header.setObjectName("header")
        layout.addWidget(header)

        title = QLabel(reminder.get("title", "Reminder"))
        title.setWordWrap(True)
        f = title.font(); f.setPointSize(14); f.setBold(True); title.setFont(f)
        layout.addWidget(title)

        due_lbl = QLabel(f"due: {reminder.get('due_at', '?')}")
        due_lbl.setObjectName("muted")
        layout.addWidget(due_lbl)

        note = (reminder.get("note") or "").strip()
        if note:
            note_lbl = QLabel(note[:200])
            note_lbl.setObjectName("muted")
            note_lbl.setWordWrap(True)
            layout.addWidget(note_lbl)

        layout.addStretch(1)

        # Button row — Snooze options + Done, none auto-default
        row = QHBoxLayout()
        for label, mins in (("+5m", 5), ("+1h", 60), ("+1d", 24 * 60)):
            btn = QPushButton(label)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setAutoDefault(False); btn.setDefault(False)
            btn.clicked.connect(lambda _=False, m=mins: self._snooze(m))
            row.addWidget(btn)
        row.addStretch(1)
        dismiss = QPushButton("Dismiss")
        dismiss.setCursor(Qt.PointingHandCursor)
        dismiss.setAutoDefault(False); dismiss.setDefault(False)
        dismiss.clicked.connect(self._dismiss)
        row.addWidget(dismiss)
        done = QPushButton("Done")
        done.setObjectName("primary")
        done.setCursor(Qt.PointingHandCursor)
        done.setAutoDefault(False); done.setDefault(False)
        done.clicked.connect(self._done)
        row.addWidget(done)
        layout.addLayout(row)

        _place_top_right(self)

    def _done(self):
        try:
            self._on_done()
        except Exception:
            pass
        self.hide(); self.deleteLater()

    def _snooze(self, minutes: int):
        try:
            self._on_snooze(minutes)
        except Exception:
            pass
        self.hide(); self.deleteLater()

    def _dismiss(self):
        try:
            self._on_dismiss()
        except Exception:
            pass
        self.hide(); self.deleteLater()
