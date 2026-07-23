"""Clickable human-body map for the FITNESS tab.

Renders a front + back silhouette in a QGraphicsView. Each muscle group
is a separate QGraphicsPathItem so we get pixel-perfect hit testing and
per-part fill color updates.

Color states per part:
  GREEN  — trained this ISO week (union of workout_log focus rollup +
           body_part_log direct entries)
  RED    — missed all of last ISO week AND still missing this week
           (carry-forward warning: "you skipped this last week, do it now")
  GREY   — untrained this week, but not a carry-forward
  outline — always a hairline in the palette border color

Click flow:
  1. User clicks a part.
  2. Confirm dialog: "Did you train <part> today?"
     - Yes → tracker.log_body_part(part); part turns green.
     - No  → nothing.
  3. If the part is already green, the dialog offers "Undo — clear today's
     log for <part>".

No new dependencies. All shapes are QPainterPaths; the whole widget is
one QGraphicsView with a fixed logical coordinate system (400x600) that
scales to whatever size the container allocates.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from PySide6.QtCore import Qt, QRectF, Signal
from PySide6.QtGui import (
    QBrush, QColor, QPainterPath, QPen, QFont,
)
from PySide6.QtWidgets import (
    QGraphicsPathItem, QGraphicsScene, QGraphicsSimpleTextItem, QGraphicsView,
    QMessageBox, QWidget,
)


# ---------- palette (matches Linear theme in panel.py NEON dict) ----------
_GREEN         = QColor("#4CB782")
_GREEN_HOVER   = QColor("#5FCC96")
_RED           = QColor("#EB5757")
_RED_HOVER     = QColor("#FF6D6D")
_GREY          = QColor("#26262E")
_GREY_HOVER    = QColor("#3A3A44")
_BORDER        = QColor("#3A3A44")
_TEXT          = QColor("#FFFFFF")
_TEXT_DIM      = QColor("#B4BABF")


@dataclass(frozen=True)
class _PartShape:
    """One clickable region on the body map."""
    name: str                              # canonical lowercase, e.g. "chest"
    label: str                             # human-facing, e.g. "Chest"
    path: QPainterPath
    label_pos: tuple[float, float]         # centered at (x, y) in logical coords


def _rounded(x: float, y: float, w: float, h: float, r: float = 6) -> QPainterPath:
    p = QPainterPath()
    p.addRoundedRect(QRectF(x, y, w, h), r, r)
    return p


def _ellipse(cx: float, cy: float, rx: float, ry: float) -> QPainterPath:
    p = QPainterPath()
    p.addEllipse(QRectF(cx - rx, cy - ry, rx * 2, ry * 2))
    return p


def _heart(cx: float, cy: float, size: float = 20) -> QPainterPath:
    """Simple heart glyph for the 'cardio' region."""
    s = size / 2
    p = QPainterPath()
    p.moveTo(cx, cy + s * 0.9)
    p.cubicTo(cx - s * 1.6, cy - s * 0.3,
              cx - s * 0.5, cy - s * 1.2,
              cx,           cy - s * 0.2)
    p.cubicTo(cx + s * 0.5, cy - s * 1.2,
              cx + s * 1.6, cy - s * 0.3,
              cx,           cy + s * 0.9)
    return p


def _build_shapes() -> list[_PartShape]:
    """Front view on the left (0-200), back view on the right (200-400).
    Cardio (heart) sits between them at the top.
    Coordinate system: 400 wide, 600 tall — QGraphicsView scales to fit."""
    shapes: list[_PartShape] = []

    # --- FRONT (left side, centered around x=100) ---------------------------
    # Chest (upper torso)
    shapes.append(_PartShape("chest",     "Chest",
        _rounded(72, 100, 56, 46, 10), (100, 122)))
    # Shoulders — two round caps at the top of the arms. Label OUTSIDE
    # to the left of the left shoulder so it doesn't crowd the chest.
    shapes.append(_PartShape("shoulders", "Shoulders",
        _ellipse(52, 108, 14, 14).united(_ellipse(148, 108, 14, 14)),
        (12, 108)))
    # Biceps — inner upper arm rectangles. Label to the LEFT of the left
    # bicep so it doesn't overlap chest/core.
    shapes.append(_PartShape("biceps",    "Biceps",
        _rounded(38, 130, 20, 44, 6).united(_rounded(142, 130, 20, 44, 6)),
        (12, 152)))
    # Core (mid torso)
    shapes.append(_PartShape("core",      "Core / Abs",
        _rounded(78, 152, 44, 60, 10), (100, 182)))
    # Quads (upper legs, front)
    shapes.append(_PartShape("quads",     "Quads",
        _rounded(76, 226, 22, 90, 8).united(_rounded(102, 226, 22, 90, 8)),
        (100, 270)))

    # --- BACK (right side, centered around x=300) ---------------------------
    # Back (upper + mid torso from behind)
    shapes.append(_PartShape("back",      "Back",
        _rounded(272, 100, 56, 110, 10), (300, 195)))
    # Rear delts — two round caps behind shoulders, label OUTSIDE to the
    # right so it doesn't collide with the arm labels
    shapes.append(_PartShape("rear delts", "Rear delts",
        _ellipse(252, 108, 14, 14).united(_ellipse(348, 108, 14, 14)),
        (395, 108)))
    # Triceps — outer upper arm rectangles on the back view.
    # Label above the RIGHT arm so it doesn't cover the Back area.
    shapes.append(_PartShape("triceps",   "Triceps",
        _rounded(232, 130, 18, 44, 6).united(_rounded(350, 130, 18, 44, 6)),
        (395, 152)))
    # Glutes
    shapes.append(_PartShape("glutes",    "Glutes",
        _rounded(272, 216, 56, 46, 12), (300, 238)))
    # Hamstrings (back of upper legs)
    shapes.append(_PartShape("hamstrings", "Hamstrings",
        _rounded(276, 266, 22, 60, 8).united(_rounded(302, 266, 22, 60, 8)),
        (300, 296)))
    # Calves (back of lower legs)
    shapes.append(_PartShape("calves",    "Calves",
        _rounded(276, 336, 22, 70, 8).united(_rounded(302, 336, 22, 70, 8)),
        (300, 370)))

    # --- CARDIO — heart glyph between the two silhouettes ------------------
    shapes.append(_PartShape("cardio",    "Cardio",
        _heart(200, 130, size=32), (200, 165)))

    return shapes


# ---------- INTERACTIVE PART ITEM ------------------------------------------

class _PartItem(QGraphicsPathItem):
    """Clickable body-part region. Emits via a callback on click; owns its
    own hover-highlight and state-color logic."""

    def __init__(self, shape: _PartShape, on_click: Callable[[str], None]):
        super().__init__(shape.path)
        self._shape = shape
        self._on_click = on_click
        self._state = "grey"                      # 'green' | 'red' | 'grey'
        self._hover = False
        self.setAcceptHoverEvents(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setPen(QPen(_BORDER, 1.2))
        self.setToolTip(f"{shape.label}  —  click to log")
        self._apply_brush()

    def name(self) -> str:
        return self._shape.name

    def set_state(self, state: str) -> None:
        assert state in ("green", "red", "grey")
        self._state = state
        self._apply_brush()

    def _apply_brush(self) -> None:
        base = {"green": _GREEN, "red": _RED, "grey": _GREY}[self._state]
        if self._hover:
            hover_map = {"green": _GREEN_HOVER, "red": _RED_HOVER, "grey": _GREY_HOVER}
            base = hover_map[self._state]
        self.setBrush(QBrush(base))

    def hoverEnterEvent(self, e):
        self._hover = True; self._apply_brush(); super().hoverEnterEvent(e)

    def hoverLeaveEvent(self, e):
        self._hover = False; self._apply_brush(); super().hoverLeaveEvent(e)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._on_click(self._shape.name)
        super().mousePressEvent(e)


# ---------- MAIN WIDGET -----------------------------------------------------

class BodyMapWidget(QGraphicsView):
    """The clickable human body map.

    Exposes .refresh() to redraw state based on current week_coverage + last
    week's missing list. Emits `partClicked(part_name)` for the container to
    handle the confirm-dialog + tracker.log flow (kept here for a self-
    contained widget, but signal lets tests intercept)."""

    partClicked = Signal(str)                   # emitted after click, pre-dialog
    changed = Signal()                          # emitted after a log/unlog

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setRenderHint(self.renderHints().Antialiasing if hasattr(self, "renderHints") else 0x1)
        # Antialiasing on all painters for smooth silhouette
        from PySide6.QtGui import QPainter
        self.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing
                            | QPainter.SmoothPixmapTransform)
        self.setStyleSheet("QGraphicsView { background: #141419; border: 1px solid #26262E; border-radius: 8px; }")
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setMinimumHeight(360)
        self.setMinimumWidth(420)

        self._scene = QGraphicsScene(self)
        # Wider than 400 to give outside labels ("Shoulders", "Rear delts",
        # "Triceps") room to breathe without clipping.
        self._scene.setSceneRect(-40, 0, 480, 460)
        self.setScene(self._scene)

        # Silhouette background — head circles + hint labels above each half.
        # These are decorative, not clickable.
        self._add_decoration()

        # Interactive parts
        self._items: dict[str, _PartItem] = {}
        for shp in _build_shapes():
            item = _PartItem(shp, on_click=self._handle_click)
            self._scene.addItem(item)
            self._items[shp.name] = item
            self._add_label(shp.label, shp.label_pos)

        self.refresh()

    # --- construction helpers ---------------------------------------------

    def _add_decoration(self) -> None:
        # Section headers
        for txt, x in (("FRONT", 100), ("BACK", 300)):
            t = QGraphicsSimpleTextItem(txt)
            f = QFont("Inter", 9, QFont.Bold)
            f.setLetterSpacing(QFont.AbsoluteSpacing, 1.5)
            t.setFont(f)
            t.setBrush(QBrush(_TEXT_DIM))
            br = t.boundingRect()
            t.setPos(x - br.width() / 2, 8)
            self._scene.addItem(t)
        # Heads (non-interactive)
        head_pen = QPen(_BORDER, 1.2)
        for cx in (100, 300):
            head = self._scene.addEllipse(
                cx - 22, 42, 44, 52, head_pen, QBrush(_GREY)
            )
            head.setZValue(-1)

    def _add_label(self, text: str, pos: tuple[float, float]) -> None:
        t = QGraphicsSimpleTextItem(text)
        f = QFont("Inter", 8)
        t.setFont(f)
        t.setBrush(QBrush(_TEXT))
        br = t.boundingRect()
        t.setPos(pos[0] - br.width() / 2, pos[1] - br.height() / 2)
        t.setZValue(10)
        # Labels shouldn't intercept clicks
        t.setAcceptedMouseButtons(Qt.NoButton)
        self._scene.addItem(t)

    # --- state refresh ----------------------------------------------------

    def refresh(self) -> None:
        """Re-read this week's coverage + last week's misses and recolor."""
        from . import coach as fcoach
        cov = fcoach.week_coverage()
        trained_this_week = set(cov["trained"])
        missed_last_week = fcoach.last_week_missing()
        for name, item in self._items.items():
            if name in trained_this_week:
                item.set_state("green")
            elif name in missed_last_week:
                item.set_state("red")
            else:
                item.set_state("grey")

    # --- click handler ----------------------------------------------------

    def _handle_click(self, part: str) -> None:
        self.partClicked.emit(part)
        from . import tracker
        from . import coach as fcoach
        current_state = self._items[part]._state
        label = self._items[part]._shape.label

        if current_state == "green":
            # Offer undo
            confirm = QMessageBox(self)
            confirm.setWindowTitle("Already logged")
            confirm.setText(f"You've marked <b>{label}</b> as trained this week.")
            confirm.setInformativeText(
                "Undo today's log for this body part?"
            )
            undo = confirm.addButton("Undo today", QMessageBox.DestructiveRole)
            keep = confirm.addButton("Keep it",    QMessageBox.RejectRole)
            confirm.exec()
            if confirm.clickedButton() is undo:
                tracker.unlog_body_part(part)
                self.refresh()
                self.changed.emit()
            return

        # Not green — offer to log
        was_carry = current_state == "red"
        confirm = QMessageBox(self)
        confirm.setWindowTitle("Log workout")
        confirm.setText(f"Did you train <b>{label}</b> today?")
        if was_carry:
            confirm.setInformativeText(
                "This was skipped last week — logging it now clears the "
                "carry-forward warning."
            )
        yes = confirm.addButton("Yes, mark done", QMessageBox.AcceptRole)
        no  = confirm.addButton("Not yet",        QMessageBox.RejectRole)
        for btn in (yes, no):
            btn.setAutoDefault(False); btn.setDefault(False)
        confirm.exec()
        if confirm.clickedButton() is yes:
            tracker.log_body_part(part)
            self.refresh()
            self.changed.emit()

    # --- proper Qt resize behavior: keep the silhouette fitted ------------

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.fitInView(self._scene.sceneRect(), Qt.KeepAspectRatio)

    def showEvent(self, e):
        super().showEvent(e)
        self.fitInView(self._scene.sceneRect(), Qt.KeepAspectRatio)
