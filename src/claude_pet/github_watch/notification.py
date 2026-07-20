"""Toast-style GitHub activity notification.

When a watched repo lands a new commit / PR / review / release / deploy,
the pet plays a sound AND slides a small notification card in from the
top-right corner so the developer can SEE what happened without opening
the dashboard. Matches the ergonomics-overlay visual language (deep navy,
cyan hairline, monospace) so the two never look out of place next to
each other.

Auto-dismisses after `AUTO_HIDE_SECONDS`. Click the card to open the
event's GitHub URL in the default browser. Click the ✕ to dismiss.
"""

from __future__ import annotations

import sys
import webbrowser
from typing import Optional

from PySide6.QtCore import Qt, QTimer, QPropertyAnimation, QRect, QEasingCurve
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)


# Reaction → color + label prefix. Mirrors panel.py NEON and classify.py.
_REACTION_STYLE = {
    "success": {"accent": "#4ADE80", "label": "SUCCESS", "glyph": "●"},
    "error":   {"accent": "#F87171", "label": "ATTENTION", "glyph": "▲"},
    "curious": {"accent": "#4FC3F7", "label": "ACTIVITY", "glyph": "◆"},
    "none":    {"accent": "#5A7099", "label": "ACTIVITY", "glyph": "○"},
}


AUTO_HIDE_SECONDS = 8
CARD_WIDTH = 360
CARD_HEIGHT = 96


class _AutoHideBar(QWidget):
    """A hairline progress bar at the top edge — countdown to auto-dismiss.
    Same visual family as the ergonomics overlay's _AutoCloseBar so the two
    surfaces read as siblings."""

    def __init__(self, total_s: float, accent_hex: str, parent=None):
        super().__init__(parent)
        self.total_s = max(0.1, total_s)
        self.remaining = float(total_s)
        self.accent = QColor(accent_hex)
        self.setFixedHeight(3)

    def set_remaining(self, seconds: float) -> None:
        self.remaining = max(0.0, seconds)
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        # Track — barely-visible navy line
        p.fillRect(self.rect(), QColor("#1B3A70"))
        # Filled portion — matches event reaction color
        w = int(self.width() * (self.remaining / self.total_s))
        p.fillRect(0, 0, w, self.height(), self.accent)


class GithubActivityToast(QWidget):
    """Small non-blocking toast that mirrors the ergonomics overlay's style.

    Frameless, always-on-top, translucent-safe, no focus theft, no Dock
    activation. Slides in from the right edge on show, auto-dismisses in
    ~8 seconds, clickable to open the GitHub URL.
    """

    def __init__(
        self,
        title: str,
        repo_slug: str,
        reaction: str = "curious",
        url: Optional[str] = None,
        auto_hide_seconds: float = AUTO_HIDE_SECONDS,
    ):
        super().__init__(None)
        self._url = url
        style = _REACTION_STYLE.get(reaction, _REACTION_STYLE["curious"])
        accent = style["accent"]

        self.setWindowTitle("Claude Pet — GitHub Activity")
        # Deliberately NOT using Qt.Tool: on macOS, Qt.Tool windows are
        # auto-hidden when the owning app deactivates. Since the pet runs
        # with activationPolicy=accessory it is NEVER the active app, so
        # Qt.Tool would dismiss the toast the instant it appears.
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        # Solid card — no translucency; readable in every backdrop.
        self.setStyleSheet(
            f"QWidget#card {{ "
            f"  background: #061027; "
            f"  border: 1px solid #1B3A70; "
            f"  border-left: 3px solid {accent}; "
            f"  border-radius: 12px; "
            f"}} "
            f"QLabel {{ color: #DDEBFF; background: transparent; "
            f"font-family: 'JetBrains Mono', Menlo, monospace; }} "
            f"QLabel#header {{ color: {accent}; font-size: 10px; "
            f"letter-spacing: 1.5px; }} "
            f"QLabel#repo {{ color: #8CA9D6; font-size: 11px; }} "
            f"QLabel#title {{ color: #DDEBFF; font-size: 13px; }} "
            f"QPushButton#close {{ background: transparent; color: #5A7099; "
            f"border: none; font-size: 14px; font-weight: 700; "
            f"padding: 0 6px; }} "
            f"QPushButton#close:hover {{ color: {accent}; }} "
        )
        self.setFixedSize(CARD_WIDTH, CARD_HEIGHT)

        # Root container so QSS border-left applies without the outer window
        # rules overriding it.
        card = QWidget(self)
        card.setObjectName("card")
        card.setGeometry(0, 0, CARD_WIDTH, CARD_HEIGHT)

        outer = QVBoxLayout(card)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.ring = _AutoHideBar(auto_hide_seconds, accent)
        outer.addWidget(self.ring)

        body = QVBoxLayout()
        body.setContentsMargins(14, 8, 10, 10)
        body.setSpacing(3)

        top_row = QHBoxLayout()
        top_row.setSpacing(6)
        header = QLabel(f"{style['glyph']} GITHUB · {style['label']}")
        header.setObjectName("header")
        top_row.addWidget(header)
        top_row.addStretch(1)
        close = QPushButton("×")
        close.setObjectName("close")
        close.setCursor(Qt.PointingHandCursor)
        close.setFixedSize(22, 22)
        close.clicked.connect(self._dismiss)
        top_row.addWidget(close)
        body.addLayout(top_row)

        repo_lbl = QLabel(repo_slug)
        repo_lbl.setObjectName("repo")
        body.addWidget(repo_lbl)

        # Title — the human summary from classify.classify(). Wrap and clamp.
        title_lbl = QLabel(_ellipsize(title, 96))
        title_lbl.setObjectName("title")
        title_lbl.setWordWrap(True)
        body.addWidget(title_lbl)

        outer.addLayout(body)

        self._decided = False
        self._remaining = float(auto_hide_seconds)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(100)

        self._place_top_right()

    def _place_top_right(self):
        """Anchor at the top-right so the pet (bottom-right) stays uncovered."""
        screen = QGuiApplication.primaryScreen().availableGeometry()
        margin = 20
        x = screen.right() - self.width() - margin
        y = screen.top() + margin
        self.move(x, y)
        # Slide-in animation: come from just off-screen to the anchor.
        self._anim = QPropertyAnimation(self, b"geometry")
        self._anim.setDuration(220)
        self._anim.setStartValue(QRect(screen.right() + 20, y,
                                        self.width(), self.height()))
        self._anim.setEndValue(QRect(x, y, self.width(), self.height()))
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.start()

    def showEvent(self, event):
        """Once the NSWindow exists, apply the macOS pin so it (a) doesn't
        hide on app deactivate and (b) survives Space switches."""
        super().showEvent(event)
        self._pin_this_window_macos()

    def _pin_this_window_macos(self):
        if sys.platform != "darwin":
            return
        try:
            from AppKit import (
                NSApp,
                NSWindowCollectionBehaviorCanJoinAllSpaces,
                NSWindowCollectionBehaviorStationary,
                NSWindowCollectionBehaviorFullScreenAuxiliary,
                NSWindowCollectionBehaviorIgnoresCycle,
            )
            behavior = (
                NSWindowCollectionBehaviorCanJoinAllSpaces
                | NSWindowCollectionBehaviorStationary
                | NSWindowCollectionBehaviorFullScreenAuxiliary
                | NSWindowCollectionBehaviorIgnoresCycle
            )
            # Match PetWindow._MACOS_TARGET_LEVEL exactly. Toast sits at the
            # same level as the pet — both above every user-space window.
            for w in NSApp().windows():
                try:
                    # Skip hidden windows — resurrecting them (e.g. dismissed
                    # BreakOverlays) shows empty grey squares over the pet.
                    if not w.isVisible():
                        continue
                    w.setLevel_(1500)
                    w.setCollectionBehavior_(behavior)
                    w.setHidesOnDeactivate_(False)
                    w.orderFrontRegardless()
                except Exception:
                    pass
        except Exception:
            pass

    def _tick(self):
        self._remaining = max(0.0, self._remaining - 0.1)
        self.ring.set_remaining(self._remaining)
        if self._remaining <= 0.0:
            self._dismiss()

    def mousePressEvent(self, event):
        """Click anywhere on the card (except the ×) to open the URL."""
        if event.button() == Qt.LeftButton and self._url:
            try:
                webbrowser.open(self._url)
            except Exception:
                pass
            self._dismiss()

    def _dismiss(self):
        if self._decided:
            return
        self._decided = True
        try:
            self._timer.stop()
        except Exception:
            pass
        self.close()


def _ellipsize(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"
