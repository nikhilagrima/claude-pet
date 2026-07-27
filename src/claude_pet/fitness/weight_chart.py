"""Weight-tracker line chart for the FITNESS tab.

Renders the last N days of weight_log rows as a line chart with a
dashed horizontal target-weight reference. Zero new dependencies —
plain QPainter on a QWidget, same technique as the memory graph.

Layout (logical coords, scales to widget size):
  - Top gutter: title + latest weight + weekly delta
  - Left gutter: y-axis kg labels (min / max)
  - Body: line chart with dots at each data point
  - Bottom gutter: x-axis start/end dates
  - Overlay: dashed line at target_weight_kg with kg label
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

from PySide6.QtCore import Qt, QPointF, QRectF
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QWidget


# Linear palette — mirrors NEON dict in panel.py
_BG          = QColor("#141419")
_GRID        = QColor("#26262E")
_LINE        = QColor("#5E6AD2")           # accent purple
_DOT         = QColor("#7A7EEA")
_DOT_TODAY   = QColor("#4CB782")           # highlight today's dot
_TARGET      = QColor("#B78AEC")           # softer lavender for target line
_TEXT        = QColor("#FFFFFF")
_TEXT_DIM    = QColor("#B4BABF")
_TEXT_MUTED  = QColor("#6E7079")
_RED         = QColor("#EB5757")
_GREEN       = QColor("#4CB782")


@dataclass(frozen=True)
class _Point:
    day: date
    weight_kg: float


class WeightChart(QWidget):
    """Line chart of the last N days of weight logs.

    Call .refresh() after logging a new weight or on tab focus.
    The widget sizes itself to whatever the layout gives it and
    redraws on resize.
    """

    def __init__(self, days: int = 30, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._days = days
        self._points: list[_Point] = []
        self._target_kg: Optional[float] = None
        self._current_kg: Optional[float] = None
        self.setMinimumHeight(180)
        self.setMinimumWidth(320)
        self.refresh()

    # --- data -------------------------------------------------------------

    def refresh(self) -> None:
        from . import config as fcfg
        from . import tracker
        prof = fcfg.profile()
        try:
            self._target_kg = float(prof.get("target_weight_kg") or 0) or None
        except Exception:
            self._target_kg = None
        try:
            self._current_kg = float(prof.get("weight_kg") or 0) or None
        except Exception:
            self._current_kg = None

        recent = tracker.recent(days=self._days)
        # tracker returns newest-first; chart wants oldest→newest
        raw = list(reversed(recent["weights"]))
        self._points = []
        for r in raw:
            try:
                d = date.fromisoformat(r["day"])
                self._points.append(_Point(d, float(r["weight_kg"])))
            except Exception:
                continue
        self.update()

    # --- paint ------------------------------------------------------------

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)

        # Background card
        p.fillRect(self.rect(), _BG)
        # Rounded border
        p.setPen(QPen(_GRID, 1))
        p.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 8, 8)

        pad_l, pad_r, pad_t, pad_b = 44, 16, 42, 26
        w = self.width() - pad_l - pad_r
        h = self.height() - pad_t - pad_b
        if w < 40 or h < 40:
            return
        chart_rect = QRectF(pad_l, pad_t, w, h)

        # --- title / header row ------------------------------------------
        f_title = QFont("Inter", 10, QFont.Bold)
        p.setFont(f_title)
        p.setPen(_TEXT)
        p.drawText(pad_l, 18, "Weight (kg)")

        f_meta = QFont("Inter", 9)
        p.setFont(f_meta)
        p.setPen(_TEXT_DIM)
        header_meta = self._header_text()
        # right-align the meta text
        fm = p.fontMetrics()
        meta_w = fm.horizontalAdvance(header_meta)
        p.drawText(self.width() - pad_r - meta_w, 18, header_meta)

        if not self._points:
            p.setPen(_TEXT_MUTED)
            p.setFont(QFont("Inter", 10))
            msg = "No weight logs yet — use the Log Weight button below."
            fm = p.fontMetrics()
            tw = fm.horizontalAdvance(msg)
            p.drawText((self.width() - tw) // 2,
                       self.height() // 2 + 5, msg)
            return

        # --- y-axis range -------------------------------------------------
        weights = [pt.weight_kg for pt in self._points]
        if self._target_kg is not None:
            weights.append(self._target_kg)
        y_min = min(weights) - 1.0
        y_max = max(weights) + 1.0
        if y_max - y_min < 2.0:
            mid = (y_max + y_min) / 2.0
            y_min, y_max = mid - 1.0, mid + 1.0
        y_range = y_max - y_min

        def y_for(kg: float) -> float:
            return chart_rect.bottom() - (kg - y_min) / y_range * chart_rect.height()

        # x positions: evenly spaced along chart width, oldest at left
        n = len(self._points)
        def x_for(i: int) -> float:
            if n == 1:
                return chart_rect.left() + chart_rect.width() / 2
            return chart_rect.left() + (chart_rect.width() * i / (n - 1))

        # --- grid lines (3 horizontal + labels) --------------------------
        p.setPen(QPen(_GRID, 1, Qt.DashLine))
        for frac in (0.0, 0.5, 1.0):
            y = chart_rect.bottom() - frac * chart_rect.height()
            p.drawLine(QPointF(chart_rect.left(), y),
                       QPointF(chart_rect.right(), y))
            kg = y_min + frac * y_range
            p.setPen(_TEXT_MUTED)
            p.setFont(QFont("Inter", 8))
            p.drawText(4, y + 4, f"{kg:.1f}")
            p.setPen(QPen(_GRID, 1, Qt.DashLine))

        # --- target reference line --------------------------------------
        if self._target_kg is not None:
            ty = y_for(self._target_kg)
            p.setPen(QPen(_TARGET, 1.4, Qt.DashLine))
            p.drawLine(QPointF(chart_rect.left(), ty),
                       QPointF(chart_rect.right(), ty))
            p.setPen(_TARGET)
            p.setFont(QFont("Inter", 8, QFont.Bold))
            label = f"target {self._target_kg:.1f}"
            fm = p.fontMetrics()
            lw = fm.horizontalAdvance(label)
            p.drawText(chart_rect.right() - lw - 4, ty - 4, label)

        # --- polyline through data points --------------------------------
        p.setPen(QPen(_LINE, 2))
        prev = None
        for i, pt in enumerate(self._points):
            cur = QPointF(x_for(i), y_for(pt.weight_kg))
            if prev is not None:
                p.drawLine(prev, cur)
            prev = cur

        # --- data-point dots + today highlight ---------------------------
        today = date.today()
        for i, pt in enumerate(self._points):
            cx, cy = x_for(i), y_for(pt.weight_kg)
            is_today = (pt.day == today)
            r = 4.5 if is_today else 3.0
            p.setPen(Qt.NoPen)
            p.setBrush(_DOT_TODAY if is_today else _DOT)
            p.drawEllipse(QPointF(cx, cy), r, r)

        # --- x-axis end labels -------------------------------------------
        p.setPen(_TEXT_MUTED)
        p.setFont(QFont("Inter", 8))
        first, last = self._points[0].day, self._points[-1].day
        p.drawText(chart_rect.left(), chart_rect.bottom() + 16,
                   first.strftime("%b %-d"))
        end_str = last.strftime("%b %-d")
        fm = p.fontMetrics()
        p.drawText(chart_rect.right() - fm.horizontalAdvance(end_str),
                   chart_rect.bottom() + 16, end_str)

        # If we have at least 2 points, show delta in bottom-left corner
        if len(self._points) >= 2:
            first_kg = self._points[0].weight_kg
            last_kg = self._points[-1].weight_kg
            delta = last_kg - first_kg
            color = _GREEN if delta < 0 else (_RED if delta > 0 else _TEXT_DIM)
            arrow = "↓" if delta < 0 else ("↑" if delta > 0 else "→")
            label = f"{arrow} {abs(delta):.1f} kg over {len(self._points)} readings"
            p.setPen(color)
            p.setFont(QFont("Inter", 9, QFont.Bold))
            p.drawText(pad_l, self.height() - 6, label)

    def _header_text(self) -> str:
        parts: list[str] = []
        if self._points:
            latest = self._points[-1].weight_kg
            parts.append(f"latest {latest:.1f} kg")
        if self._target_kg is not None:
            parts.append(f"target {self._target_kg:.1f} kg")
        if self._points and self._target_kg is not None:
            to_go = self._points[-1].weight_kg - self._target_kg
            if abs(to_go) > 0.05:
                parts.append(
                    f"{'to lose' if to_go > 0 else 'below target'} "
                    f"{abs(to_go):.1f} kg"
                )
            else:
                parts.append("at goal")
        return "  ·  ".join(parts)
