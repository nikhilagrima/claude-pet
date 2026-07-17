"""Click-to-open panel: 4 tabs — Projects, Graph, Skills, Stats.

Uses only PySide6 widgets we already have (no QWebEngineView, keeps the
dependency footprint at zero-new). The Graph tab renders a self-contained
force-layout drawing via QPainter (no HTML), so this works on every OS with
just the base PySide6 install."""

from __future__ import annotations

import json
import math
import os
import random
from datetime import datetime, timezone

from PySide6.QtCore import Qt, QPointF, QTimer, QRectF
from PySide6.QtGui import (
    QAction, QBrush, QColor, QFont, QFontDatabase, QPainter, QPen, QPixmap,
)
from PySide6.QtWidgets import (
    QApplication, QDialog, QGraphicsDropShadowEffect, QGraphicsEllipseItem,
    QGraphicsLineItem, QGraphicsScene, QGraphicsSimpleTextItem, QGraphicsView,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QTabWidget,
    QVBoxLayout, QWidget,
)

from . import memory


# ---------- futuristic HUD design tokens ----------
# Deep-navy HUD/FUI palette from the reference — hairline cyan on dark blue,
# tiny monospace data readouts, corner-bracket panel decorators.
NEON = {
    "bg_deep":    "#061027",
    "bg_panel":   "#0A1A38",
    "bg_card":    "#0F2140",
    "bg_hover":   "#152A55",
    "border":     "#1B3A70",     # hairline grid tone
    "border_hi":  "#4FC3F7",     # bright cyan on interactive
    "cyan":       "#4FC3F7",
    "cyan_hi":    "#7DE3FF",     # for glow highlights
    "blue":       "#3FA3FF",
    "magenta":    "#F0ABFC",     # rare accent
    "green":      "#4ADE80",
    "orange":     "#F97316",
    "red":        "#F87171",
    "text":       "#DDEBFF",
    "text_dim":   "#8CA9D6",
    "text_muted": "#5A7099",
}

# Use a mono/tech font — falls back gracefully across OSes.
def _neon_font() -> str:
    return "JetBrains Mono, SF Mono, Menlo, Consolas, monospace"


TIER_COLOR = {
    "hatchling":  NEON["cyan"],
    "apprentice": NEON["blue"],
    "senior":     NEON["magenta"],
    "ponytail":   NEON["orange"],
}
TIER_ICON = {
    "hatchling":  "◇",
    "apprentice": "◆",
    "senior":     "❖",
    "ponytail":   "✦",
}


def _apply_neon_glow(widget: QWidget, color: str = None, radius: int = 22,
                     offset: int = 0) -> QGraphicsDropShadowEffect:
    """Attach a colored drop-shadow so the widget appears to glow."""
    eff = QGraphicsDropShadowEffect(widget)
    c = QColor(color or NEON["cyan"])
    c.setAlpha(180)
    eff.setColor(c)
    eff.setBlurRadius(radius)
    eff.setOffset(offset, offset)
    widget.setGraphicsEffect(eff)
    return eff


class _CornerBrackets(QWidget):
    """Four subtle L-brackets — signature futuristic accent, nothing else."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_NoSystemBackground)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        pen = QPen(QColor(NEON["cyan"])); pen.setWidthF(1.5); pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        L = 14; m = 4
        for (x1, y1, x2, y2, x3, y3) in (
            (m, m + L, m, m, m + L, m),                                     # top-left
            (w - m - L, m, w - m, m, w - m, m + L),                         # top-right
            (m, h - m - L, m, h - m, m + L, h - m),                         # bottom-left
            (w - m - L, h - m, w - m, h - m, w - m, h - m - L),             # bottom-right
        ):
            p.drawLine(x1, y1, x2, y2); p.drawLine(x2, y2, x3, y3)


class _StatusStrip(QWidget):
    """Minimal top strip: pulsing dot + wordmark. No ticker, no fake HUD text."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(28)
        self._pulse = 0
        t = QTimer(self); t.timeout.connect(self._blink); t.start(1200)

    def _blink(self):
        self._pulse = (self._pulse + 1) % 2
        self.update()

    def paintEvent(self, event):
        from . import __version__
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        # Pulsing dot
        dot_col = QColor(NEON["cyan"])
        dot_col.setAlpha(255 if self._pulse else 130)
        p.setBrush(dot_col); p.setPen(Qt.NoPen)
        p.drawEllipse(6, 10, 8, 8)
        # Wordmark
        p.setPen(QColor(NEON["text"]))
        f = p.font(); f.setPointSize(11); f.setBold(True); f.setFamily("Menlo"); p.setFont(f)
        p.drawText(22, 20, "CLAUDE PET")
        p.setPen(QColor(NEON["text_muted"]))
        f2 = p.font(); f2.setBold(False); f2.setPointSize(9); p.setFont(f2)
        p.drawText(122, 20, f"v{__version__}")


def _dashboard_stylesheet() -> str:
    # Do NOT set QWidget { background: transparent } — that made every tab
    # translucent and prior-tab content bled through. Set backgrounds
    # explicitly per widget class instead.
    return f"""
    QDialog {{
        background: {NEON['bg_deep']};
        color: {NEON['text']};
        font-family: {_neon_font()};
        font-size: 13px;
    }}
    QLabel {{
        background: transparent;
        color: {NEON['text']};
        font-family: {_neon_font()};
    }}

    /* Tabs — neon underline on active */
    QTabWidget::pane {{
        border: 1px solid {NEON['border']};
        border-radius: 14px;
        background: {NEON['bg_panel']};
        top: -1px;
    }}
    QTabBar::tab {{
        background: {NEON['bg_panel']};
        color: {NEON['text_dim']};
        padding: 10px 22px;
        margin-right: 4px;
        border: 1px solid {NEON['border']};
        border-radius: 8px;
        font-weight: 600;
        font-family: {_neon_font()};
        letter-spacing: 1px;
        font-size: 12px;
    }}
    QTabBar::tab:hover {{
        color: {NEON['cyan']};
        border-color: {NEON['border_hi']};
    }}
    QTabBar::tab:selected {{
        color: {NEON['cyan']};
        background: {NEON['bg_card']};
        border-color: {NEON['cyan']};
    }}

    /* Buttons — deep card with neon border on hover */
    QPushButton {{
        background: {NEON['bg_card']};
        color: {NEON['text']};
        border: 1px solid {NEON['border']};
        padding: 10px 20px;
        border-radius: 12px;
        font-weight: 600;
        font-family: {_neon_font()};
        letter-spacing: 1px;
        min-height: 24px;
    }}
    QPushButton:hover {{
        border: 1px solid {NEON['cyan']};
        color: {NEON['cyan']};
        background: {NEON['bg_hover']};
    }}
    QPushButton:pressed {{
        background: {NEON['border_hi']};
        color: {NEON['bg_deep']};
    }}
    QPushButton:disabled {{
        color: {NEON['text_muted']};
        border-color: {NEON['border']};
    }}
    QPushButton#danger:hover {{
        border-color: {NEON['red']};
        color: {NEON['red']};
    }}
    QPushButton#primary {{
        background: {NEON['bg_hover']};
        border: 1px solid {NEON['cyan']};
        color: {NEON['cyan']};
    }}
    QPushButton#primary:hover {{
        background: {NEON['border_hi']};
        color: {NEON['bg_deep']};
    }}

    /* Tables — HUD data grid */
    QTableWidget {{
        background: {NEON['bg_panel']};
        border: 1px solid {NEON['border']};
        border-radius: 12px;
        gridline-color: {NEON['border']};
        color: {NEON['text']};
        alternate-background-color: {NEON['bg_card']};
        font-family: {_neon_font()};
        selection-background-color: {NEON['bg_hover']};
        selection-color: {NEON['cyan']};
    }}
    QHeaderView::section {{
        background: {NEON['bg_deep']};
        color: {NEON['cyan']};
        border: none;
        border-bottom: 1px solid {NEON['border_hi']};
        padding: 8px 10px;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: 1.2px;
        font-size: 10px;
    }}
    QTableWidget::item {{
        padding: 8px 6px;
    }}
    QTableWidget::item:selected {{
        background: {NEON['bg_hover']};
        color: {NEON['cyan']};
    }}

    /* Lists — same HUD feel */
    QListWidget {{
        background: {NEON['bg_panel']};
        border: 1px solid {NEON['border']};
        border-radius: 12px;
        color: {NEON['text']};
        padding: 6px;
        font-family: {_neon_font()};
    }}
    QListWidget::item {{
        background: {NEON['bg_card']};
        border: 1px solid {NEON['border']};
        border-radius: 10px;
        padding: 12px 14px;
        margin: 4px 2px;
    }}
    QListWidget::item:hover {{
        border-color: {NEON['border_hi']};
        background: {NEON['bg_hover']};
    }}
    QListWidget::item:selected {{
        border-color: {NEON['cyan']};
        color: {NEON['cyan']};
        background: {NEON['bg_hover']};
    }}

    /* Scrollbars */
    QScrollBar:vertical {{
        background: {NEON['bg_deep']};
        width: 10px;
        border-radius: 5px;
    }}
    QScrollBar::handle:vertical {{
        background: {NEON['border']};
        border-radius: 5px;
        min-height: 30px;
    }}
    QScrollBar::handle:vertical:hover {{
        background: {NEON['border_hi']};
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
        background: none; height: 0;
    }}
    """


class ProjectsTab(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        self.table = QTableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["Project", "Sessions", "Tool calls", "Last seen"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        layout.addWidget(self.table, 1)

        button_row = QHBoxLayout()
        self.delete_btn = QPushButton("⚠  DELETE FROM MEMORY")
        self.delete_btn.setObjectName("danger")
        self.delete_btn.setCursor(Qt.PointingHandCursor)
        self.delete_btn.clicked.connect(self._delete_selected)
        button_row.addWidget(self.delete_btn)
        button_row.addStretch(1)
        layout.addLayout(button_row)

        self._path_by_row: dict[int, str] = {}
        self.refresh()

    def refresh(self):
        rows = memory.list_projects(limit=200)
        self._path_by_row = {}
        self.table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            self._path_by_row[i] = r["path"]
            self.table.setItem(i, 0, QTableWidgetItem(r["name"] + "  —  " + r["path"]))
            self.table.setItem(i, 1, QTableWidgetItem(str(r["session_count"])))
            self.table.setItem(i, 2, QTableWidgetItem(str(r["tool_calls"])))
            self.table.setItem(i, 3, QTableWidgetItem(r["last_seen"]))
        self.table.resizeColumnsToContents()
        self.delete_btn.setEnabled(len(rows) > 0)

    def _delete_selected(self):
        row = self.table.currentRow()
        if row < 0 or row not in self._path_by_row:
            QMessageBox.information(self, "Delete project",
                                    "Pick a project first (click a row).")
            return
        path = self._path_by_row[row]
        confirm = QMessageBox.warning(
            self, "Delete project from memory",
            f"Remove every memory row for:\n\n{path}\n\n"
            "This deletes sessions, tool usage, notes, graph nodes, edges, "
            "and any skills unique to this project. It does NOT touch files "
            "on disk. Cannot be undone.",
            QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel,
        )
        if confirm != QMessageBox.Yes:
            return
        counts = memory.delete_project(path)
        summary = ", ".join(f"{k}={v}" for k, v in counts.items() if v)
        QMessageBox.information(
            self, "Deleted",
            f"Removed all memory for:\n{path}\n\n({summary or 'nothing to delete'})",
        )
        self.refresh()
        # Ask the containing panel to refresh the other tabs too.
        parent = self.parent()
        while parent is not None and not isinstance(parent, MemoryPanel):
            parent = parent.parent()
        if parent is not None:
            parent._refresh_all()


class SkillsTab(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        self.list = QListWidget()
        layout.addWidget(self.list)
        self.refresh()

    def refresh(self):
        self.list.clear()
        for s in memory.list_skills():
            icon = TIER_ICON.get(s["tier"], "·")
            desc = (s["description"] or "").split("\n")[0][:120]
            item = QListWidgetItem(
                f"{icon}  {s['title']}   (lvl {s['level']} · {s['tier']} · {s['reinforcements']}×)\n     {desc}"
            )
            item.setForeground(QColor(TIER_COLOR.get(s["tier"], "#94A3B8")))
            self.list.addItem(item)
        if self.list.count() == 0:
            self.list.addItem("No skills learned yet — keep coding, patterns get promoted at 2× reinforcement.")


class _GaugeRing(QWidget):
    """Circular percentage meter like the reference '89% MISSION PROGRESS'."""

    def __init__(self, label: str, parent=None):
        super().__init__(parent)
        self.setFixedSize(140, 140)
        self._pct = 0.0
        self._label = label
        self._value = "0"

    def set_data(self, pct: float, value_text: str):
        self._pct = max(0.0, min(1.0, pct))
        self._value = value_text
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(10, 10, self.width() - 20, self.height() - 20)
        # Outer ring track
        pen = QPen(QColor(NEON["border"])); pen.setWidthF(2)
        p.setPen(pen); p.drawArc(rect, 0, 360 * 16)
        # Filled arc
        pen = QPen(QColor(NEON["cyan"])); pen.setWidthF(4); pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        p.drawArc(rect, 90 * 16, int(-360 * 16 * self._pct))
        # Inner tick marks
        p.save()
        p.translate(self.width() / 2, self.height() / 2)
        tick_pen = QPen(QColor(NEON["border_hi"])); tick_pen.setWidthF(1)
        p.setPen(tick_pen)
        for i in range(60):
            p.rotate(6)
            long_t = i % 5 == 0
            p.drawLine(0, -50, 0, -50 + (5 if long_t else 2))
        p.restore()
        # Value + label
        p.setPen(QColor(NEON["cyan"]))
        f = p.font(); f.setPointSize(20); f.setBold(True); f.setFamily("Menlo"); p.setFont(f)
        p.drawText(self.rect(), Qt.AlignCenter, self._value)
        p.setPen(QColor(NEON["text_dim"]))
        f2 = p.font(); f2.setPointSize(8); f2.setFamily("Menlo"); p.setFont(f2)
        fm_rect = self.rect().adjusted(0, 32, 0, 0)
        p.drawText(fm_rect, Qt.AlignHCenter | Qt.AlignTop, self._label.upper())


class _DataCell(QWidget):
    """A hex-bordered stat card — label above, big value below."""

    def __init__(self, label: str, parent=None):
        super().__init__(parent)
        self.setFixedSize(160, 88)
        self._label = label
        self._value = "—"
        self._sub = ""

    def set_value(self, value: str, sub: str = ""):
        self._value = value; self._sub = sub; self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        # Card background
        p.setPen(QPen(QColor(NEON["border"]), 1))
        p.setBrush(QColor(NEON["bg_card"]))
        p.drawRoundedRect(self.rect().adjusted(2, 2, -2, -2), 8, 8)
        # Left accent bar (signature HUD element)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(NEON["cyan"]))
        p.drawRect(2, 8, 3, self.height() - 16)
        # Label
        p.setPen(QColor(NEON["text_dim"]))
        f = p.font(); f.setPointSize(8); f.setBold(True); f.setFamily("Menlo"); p.setFont(f)
        p.drawText(14, 22, self._label.upper())
        # Value
        p.setPen(QColor(NEON["cyan"]))
        f2 = p.font(); f2.setPointSize(22); f2.setBold(True); p.setFont(f2)
        p.drawText(14, 56, self._value)
        # Sub
        if self._sub:
            p.setPen(QColor(NEON["text_muted"]))
            f3 = p.font(); f3.setPointSize(8); f3.setBold(False); p.setFont(f3)
            p.drawText(14, 74, self._sub)


class StatsTab(QWidget):
    def __init__(self):
        super().__init__()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 14, 14, 14)
        outer.setSpacing(14)

        # Top row: 4 data cells
        cells = QHBoxLayout(); cells.setSpacing(10)
        self.cell_projects = _DataCell("Projects")
        self.cell_sessions = _DataCell("Sessions")
        self.cell_tools    = _DataCell("Tool calls")
        self.cell_skills   = _DataCell("Skills")
        for c in (self.cell_projects, self.cell_sessions, self.cell_tools, self.cell_skills):
            cells.addWidget(c)
        cells.addStretch(1)
        outer.addLayout(cells)

        # Middle: 2 gauges + graph info
        gauges = QHBoxLayout(); gauges.setSpacing(20)
        self.gauge_saved = _GaugeRing("tokens saved")
        self.gauge_graph = _GaugeRing("graph density")
        gauges.addWidget(self.gauge_saved)
        gauges.addWidget(self.gauge_graph)
        self.info = QLabel()
        self.info.setTextFormat(Qt.RichText); self.info.setWordWrap(True)
        gauges.addWidget(self.info, 1)
        outer.addLayout(gauges)
        outer.addStretch(1)
        self.refresh()

    def refresh(self):
        with memory.connect() as conn:
            n_projects = conn.execute("SELECT COUNT(*) c FROM projects").fetchone()["c"]
            n_sessions = conn.execute("SELECT COUNT(*) c FROM sessions").fetchone()["c"]
            n_tool_calls = conn.execute("SELECT COALESCE(SUM(tool_calls), 0) s FROM sessions").fetchone()["s"]
            n_nodes = conn.execute("SELECT COUNT(*) c FROM nodes").fetchone()["c"]
            n_edges = conn.execute("SELECT COUNT(*) c FROM edges").fetchone()["c"]
            n_skills = conn.execute("SELECT COUNT(*) c FROM skills").fetchone()["c"]
            top_tier = memory.top_tier()
        est_tokens_saved = n_nodes * 40
        # Cap at 10k tokens for the gauge; anything more = 100% full.
        saved_pct = min(1.0, est_tokens_saved / 10000)
        # Graph density: edges relative to max possible n*(n-1)/2.
        density = 0.0
        if n_nodes > 1:
            density = min(1.0, n_edges / (n_nodes * (n_nodes - 1) / 2))

        self.cell_projects.set_value(str(n_projects))
        self.cell_sessions.set_value(str(n_sessions))
        self.cell_tools.set_value(str(n_tool_calls))
        self.cell_skills.set_value(str(n_skills), f"tier: {top_tier}")

        self.gauge_saved.set_data(saved_pct, f"{est_tokens_saved // 1000}k" if est_tokens_saved >= 1000 else str(est_tokens_saved))
        self.gauge_graph.set_data(density, f"{int(density*100)}%")

        self.info.setText(
            f"<div style='color:{NEON['text_dim']}; font-family: Menlo; font-size: 11px;'>"
            f"<p style='margin:0 0 8px 0'>"
            f"<span style='color:{NEON['cyan']}; font-weight:700'>MEMORY.GRAPH</span><br>"
            f"nodes  <b style='color:{NEON['text']}'>{n_nodes}</b> &nbsp; · &nbsp; "
            f"edges  <b style='color:{NEON['text']}'>{n_edges}</b><br>"
            f"tier   <b style='color:{TIER_COLOR.get(top_tier, NEON['cyan'])}'>"
            f"{TIER_ICON.get(top_tier)}  {top_tier}</b></p>"
            f"<p style='margin:0'>"
            f"<span style='color:{NEON['cyan']}; font-weight:700'>ESTIMATE</span><br>"
            f"~{est_tokens_saved:,} tokens saved / session<br>"
            f"<span style='color:{NEON['text_muted']}'>assumes ~40 tokens per re-read avoided</span></p>"
            f"</div>"
        )


class GraphTab(QWidget):
    """Live animated force-directed graph via QGraphicsScene.

    Real physics: node-node repulsion + edge spring attraction + center gravity,
    stepped ~30 fps by QTimer. If the current project has no nodes yet, we
    render a demo graph so the tab is never dead. Pulsing accent color signals
    the graph is alive."""

    # HUD palette: everything shades of cyan/blue except the two categories
    # that need to POP (fixes and gotchas — user attention items).
    KIND_COLOR = {
        "decision":   "#4FC3F7",     # bright cyan
        "convention": "#3FA3FF",     # electric blue
        "fix":        "#F97316",     # signal orange (attention)
        "gotcha":     "#F87171",     # alert red (attention)
        "file":       "#7DE3FF",     # pale cyan
        "function":   "#60A5FA",     # sky blue
        "class":      "#22D3EE",     # cyan
        "module":     "#38BDF8",     # brighter cyan
        "concept":    "#F0ABFC",     # rare magenta accent
        "note":       "#8CA9D6",     # muted blue-grey
    }

    # Physics constants — tuned to reach a stable layout in ~200 ticks.
    REPULSION = 6000.0
    SPRING    = 0.02
    SPRING_LEN = 90.0
    GRAVITY   = 0.008
    DAMPING   = 0.85
    DT        = 0.9
    TICK_MS   = 33         # ~30fps

    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        self.info = QLabel(
            "Live memory graph — nodes attract along edges, repel each other. "
            "Weight = size, kind = color. Hover for details."
        )
        self.info.setWordWrap(True)
        layout.addWidget(self.info)
        self.view = QGraphicsView()
        self.view.setRenderHint(QPainter.Antialiasing)
        self.view.setStyleSheet(
            f"background: {NEON['bg_deep']}; "
            f"border: 1px solid {NEON['border']}; border-radius: 12px;"
        )
        self.view.setDragMode(QGraphicsView.ScrollHandDrag)
        self.view.setCursor(Qt.OpenHandCursor)
        layout.addWidget(self.view, 1)

        # Physics state — populated on refresh().
        self._positions: dict = {}
        self._velocities: dict = {}
        self._nodes: list = []
        self._edges: list = []
        self._items: dict = {}         # node id → QGraphicsEllipseItem
        self._edge_items: list = []
        self._is_demo = False
        self._pulse = 0
        self._scene = QGraphicsScene()
        self._scene.setSceneRect(-320, -220, 640, 440)
        self.view.setScene(self._scene)

        # Animation timer — starts running immediately; steps physics + redraws.
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._step)
        self._timer.start(self.TICK_MS)

        self.refresh()

    # ------------------------------------------------------------------- data
    def _load_project_graph(self):
        """Pick the most-recent project + fetch its nodes & edges."""
        projects = memory.list_projects(limit=1)
        if not projects:
            return None, [], []
        pp = projects[0]["path"]
        nodes = memory.top_nodes(pp, limit=40)
        if not nodes:
            return pp, [], []
        with memory.connect() as conn:
            edges = [
                dict(r) for r in conn.execute(
                    "SELECT src_id, dst_id, kind FROM edges WHERE project_path = ?", (pp,)
                ).fetchall()
            ]
        return pp, nodes, edges

    def _demo_graph(self):
        """A pretty static demo used when the user has no data yet."""
        demo_nodes = [
            {"id": 1,  "kind": "concept",    "value": "Your project memory will live here",   "weight": 4, "reinforcements": 1},
            {"id": 2,  "kind": "decision",   "value": "decisions the pet distills",           "weight": 3, "reinforcements": 1},
            {"id": 3,  "kind": "convention", "value": "conventions the pet notices",          "weight": 3, "reinforcements": 1},
            {"id": 4,  "kind": "fix",        "value": "fixes you've applied",                 "weight": 2, "reinforcements": 1},
            {"id": 5,  "kind": "gotcha",     "value": "gotchas to remember next time",        "weight": 2, "reinforcements": 1},
            {"id": 6,  "kind": "file",       "value": "important files (from .ua/)",          "weight": 2, "reinforcements": 1},
            {"id": 7,  "kind": "function",   "value": "important functions",                  "weight": 1, "reinforcements": 1},
            {"id": 8,  "kind": "note",       "value": "your free-form notes",                 "weight": 2, "reinforcements": 1},
        ]
        demo_edges = [
            {"src_id": 1, "dst_id": 2, "kind": "contains"},
            {"src_id": 1, "dst_id": 3, "kind": "contains"},
            {"src_id": 1, "dst_id": 4, "kind": "contains"},
            {"src_id": 1, "dst_id": 5, "kind": "contains"},
            {"src_id": 1, "dst_id": 6, "kind": "contains"},
            {"src_id": 6, "dst_id": 7, "kind": "contains"},
            {"src_id": 1, "dst_id": 8, "kind": "related"},
            {"src_id": 2, "dst_id": 4, "kind": "related"},
        ]
        return demo_nodes, demo_edges

    def refresh(self):
        pp, nodes, edges = self._load_project_graph()
        if not nodes:
            self._is_demo = True
            nodes, edges = self._demo_graph()
            self.info.setText(
                "No memory yet for this project — showing a demo. Once Claude Code "
                "runs a few sessions here, real decisions/conventions/fixes appear."
            )
        else:
            self._is_demo = False
            self.info.setText(
                f"Live memory graph for {pp} — {len(nodes)} nodes, {len(edges)} edges. "
                "Drag to pan. Hover for details."
            )

        self._nodes = nodes
        self._edges = edges

        # Seed positions on a ring so the simulation starts orderly, then relaxes.
        n = max(len(nodes), 1)
        self._positions = {}
        self._velocities = {}
        for i, node in enumerate(nodes):
            ang = 2 * math.pi * i / n
            self._positions[node["id"]] = [140 * math.cos(ang), 140 * math.sin(ang)]
            self._velocities[node["id"]] = [0.0, 0.0]

        self._rebuild_scene()

    # ------------------------------------------------------------------- draw
    def _rebuild_scene(self):
        self._scene.clear()
        self._items.clear()
        self._edge_items = []

        # Edges first (under nodes)
        edge_pen = QPen(QColor(60, 80, 130, 160))
        edge_pen.setWidthF(1.2)
        for e in self._edges:
            src = self._positions.get(e["src_id"])
            dst = self._positions.get(e["dst_id"])
            if not src or not dst:
                continue
            line = QGraphicsLineItem(src[0], src[1], dst[0], dst[1])
            line.setPen(edge_pen)
            self._scene.addItem(line)
            self._edge_items.append((line, e))

        # Nodes
        for node in self._nodes:
            p = self._positions[node["id"]]
            r = min(6 + 3 * math.log2(max(node["weight"], 1)), 22)
            color = QColor(self.KIND_COLOR.get(node["kind"], "#94A3B8"))
            ell = QGraphicsEllipseItem(p[0] - r, p[1] - r, r * 2, r * 2)
            ell.setBrush(QBrush(color))
            ell.setPen(QPen(QColor("#0A0A1F"), 1.5))
            tooltip = f"[{node['kind']}] {node.get('value','')[:200]}"
            if not self._is_demo:
                tooltip += f"\nweight={node['weight']:.1f}, reinforced={node['reinforcements']}×"
            ell.setToolTip(tooltip)
            self._scene.addItem(ell)
            self._items[node["id"]] = (ell, r, color)

    # ------------------------------------------------------------------- tick
    def _step(self):
        if not self._nodes:
            return
        self._pulse = (self._pulse + 1) % 60

        ids = [n["id"] for n in self._nodes]
        # Repulsion (all pairs).
        for i, a in enumerate(ids):
            ax, ay = self._positions[a]
            fx, fy = 0.0, 0.0
            for j, b in enumerate(ids):
                if a == b:
                    continue
                bx, by = self._positions[b]
                dx, dy = ax - bx, ay - by
                d2 = dx * dx + dy * dy + 0.01
                inv = self.REPULSION / d2
                fx += dx * inv / math.sqrt(d2)
                fy += dy * inv / math.sqrt(d2)
            # Center gravity — pull weakly toward (0,0).
            fx -= ax * self.GRAVITY * 40
            fy -= ay * self.GRAVITY * 40
            vx, vy = self._velocities[a]
            vx = (vx + fx * self.DT) * self.DAMPING
            vy = (vy + fy * self.DT) * self.DAMPING
            self._velocities[a] = [vx, vy]

        # Spring attraction along edges.
        for e in self._edges:
            src = self._positions.get(e["src_id"])
            dst = self._positions.get(e["dst_id"])
            if not src or not dst:
                continue
            dx, dy = dst[0] - src[0], dst[1] - src[1]
            dist = math.sqrt(dx * dx + dy * dy) + 0.01
            f = self.SPRING * (dist - self.SPRING_LEN)
            ux, uy = dx / dist, dy / dist
            self._velocities[e["src_id"]][0] += ux * f
            self._velocities[e["src_id"]][1] += uy * f
            self._velocities[e["dst_id"]][0] -= ux * f
            self._velocities[e["dst_id"]][1] -= uy * f

        # Integrate positions.
        max_v = 15.0
        for nid in ids:
            vx, vy = self._velocities[nid]
            # clamp velocity so a bad frame can't explode the layout
            speed = math.sqrt(vx * vx + vy * vy)
            if speed > max_v:
                vx *= max_v / speed
                vy *= max_v / speed
                self._velocities[nid] = [vx, vy]
            self._positions[nid][0] += vx
            self._positions[nid][1] += vy
            # Soft boundary (roughly the scene rect).
            self._positions[nid][0] = max(-300, min(300, self._positions[nid][0]))
            self._positions[nid][1] = max(-200, min(200, self._positions[nid][1]))

        # Update item positions + pulse the ring border of highest-weight node.
        pulse_scale = 0.85 + 0.15 * math.sin(self._pulse / 60 * 2 * math.pi)
        top_id = max(self._nodes, key=lambda n: n["weight"])["id"]
        for nid, (item, base_r, color) in self._items.items():
            p = self._positions[nid]
            r = base_r * (pulse_scale if nid == top_id else 1.0)
            item.setRect(p[0] - r, p[1] - r, r * 2, r * 2)
            # Pulse top node's outline colour too.
            if nid == top_id:
                pulse_pen = QPen(color)
                pulse_pen.setWidthF(2.0 * pulse_scale)
                item.setPen(pulse_pen)

        for line, e in self._edge_items:
            src = self._positions.get(e["src_id"])
            dst = self._positions.get(e["dst_id"])
            if src and dst:
                line.setLine(src[0], src[1], dst[0], dst[1])

    def showEvent(self, event):
        super().showEvent(event)
        if self._timer and not self._timer.isActive():
            self._timer.start(self.TICK_MS)

    def hideEvent(self, event):
        super().hideEvent(event)
        # Stop physics when tab is off-screen — no CPU when not visible.
        if self._timer and self._timer.isActive():
            self._timer.stop()


class ErgonomicsTab(QWidget):
    """Today's breaks, streak, adherence, most-skipped exercise + master toggle."""

    def __init__(self):
        super().__init__()
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(14, 14, 14, 14)
        self.layout.setSpacing(10)

        # Master toggle row — on/off + break-now buttons.
        toggle_row = QHBoxLayout()
        self.status_label = QLabel()
        toggle_row.addWidget(self.status_label)
        toggle_row.addStretch(1)
        self.toggle_btn = QPushButton("")
        self.toggle_btn.setCursor(Qt.PointingHandCursor)
        self.toggle_btn.clicked.connect(self._toggle_enabled)
        toggle_row.addWidget(self.toggle_btn)
        self.break_now_btn = QPushButton("Break now")
        self.break_now_btn.setCursor(Qt.PointingHandCursor)
        self.break_now_btn.clicked.connect(self._trigger_break_now)
        toggle_row.addWidget(self.break_now_btn)
        self.layout.addLayout(toggle_row)

        self.label = QLabel()
        self.label.setTextFormat(Qt.RichText)
        self.label.setWordWrap(True)
        self.layout.addWidget(self.label)
        self.layout.addStretch(1)
        self.refresh()

    def _toggle_enabled(self):
        from .ergonomics import config as cfg_mod
        cfg = cfg_mod.load()
        cfg["enabled"] = not cfg.get("enabled", True)
        cfg_mod.save(cfg)
        self.refresh()

    def _trigger_break_now(self):
        import urllib.request as _u, json as _json
        try:
            _u.urlopen(_u.Request(
                "http://localhost:5050/break",
                data=_json.dumps({}).encode(),
                headers={"Content-Type": "application/json"}, method="POST",
            ), timeout=1)
        except Exception:
            pass

    def refresh(self):
        from .ergonomics import tracker as t, config as cfg_mod
        cfg = cfg_mod.load()
        enabled = cfg.get("enabled", True)
        # Status pill on the left of the toggle row.
        dot_color = NEON["cyan"] if enabled else NEON["text_muted"]
        state_word = "ACTIVE" if enabled else "PAUSED"
        self.status_label.setText(
            f"<span style='color:{dot_color}; font-size:16px'>●</span> "
            f"<b style='color:{NEON['text']}; font-family:Menlo'>"
            f"ERGONOMICS COACH — {state_word}</b>"
        )
        # Toggle button label mirrors the CURRENT state → click acts on it.
        self.toggle_btn.setText("Turn OFF" if enabled else "Turn ON")
        self.toggle_btn.setObjectName("primary" if not enabled else "")
        # Re-apply stylesheet so objectName change takes effect.
        self.toggle_btn.style().unpolish(self.toggle_btn)
        self.toggle_btn.style().polish(self.toggle_btn)
        self.break_now_btn.setEnabled(enabled)

        adh = t.adherence_last_n_days(7)
        streak = t.daily_streak()
        skipped = t.most_skipped_exercise()
        today = t.today_breaks()
        completed_today = sum(1 for b in today if b["completed"])
        html = ""
        html += f"<p><b>Today:</b> {completed_today} completed of {len(today)} prompted"
        if today:
            html += "<br><small>"
            for b in today[:8]:
                mark = "✓" if b["completed"] else "✗"
                html += f"{mark} {b['ts'][11:16]} {b['category']} &nbsp; "
            html += "</small>"
        html += "</p>"
        html += (f"<p><b>Streak:</b> {streak} day{'s' if streak != 1 else ''}<br>"
                 f"<b>7-day adherence:</b> {adh['completed']}/{adh['total']} "
                 f"({adh['adherence']*100:.0f}%)</p>")
        if skipped:
            html += (f"<p><b>Most skipped:</b> {skipped[0]} ({skipped[1]}×)<br>"
                     f"<small style='color:#94A3B8'>Consider adjusting its interval "
                     f"or turning it off in the config.</small></p>")
        html += ("<hr><p style='color:#94A3B8; font-size:11px'>"
                 "Wellness guidance based on AOA / HSE / OSHA / CCOHS sources. "
                 "Not medical advice.</p>")
        self.label.setText(html)


class GithubTab(QWidget):
    """Add/remove watched repos, see recent activity, force a poll.

    All GitHub I/O happens in background threads (see app.py). This tab is
    read-mostly: it queries the local SQLite for what's known and pushes
    tiny writes (add/remove/toggle) that the background poller picks up.
    """

    def __init__(self):
        super().__init__()
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(14, 14, 14, 14)
        self.layout.setSpacing(10)

        # Row 1: add repo
        add_row = QHBoxLayout()
        add_row.setSpacing(6)
        self.add_input = QLineEdit()
        self.add_input.setPlaceholderText(
            "owner/repo   (multiple ok: facebook/react, vercel/next.js torvalds/linux)"
        )
        self.add_input.returnPressed.connect(self._add_repo)
        add_row.addWidget(self.add_input, 1)
        self.add_btn = QPushButton("+ Watch")
        self.add_btn.setObjectName("primary")
        self.add_btn.setCursor(Qt.PointingHandCursor)
        self.add_btn.clicked.connect(self._add_repo)
        add_row.addWidget(self.add_btn)
        self.check_btn = QPushButton("Poll now")
        self.check_btn.setCursor(Qt.PointingHandCursor)
        self.check_btn.clicked.connect(self._poll_now)
        add_row.addWidget(self.check_btn)
        self.layout.addLayout(add_row)

        # Row 2: watched repos table
        self.watches_table = QTableWidget(0, 4)
        self.watches_table.setHorizontalHeaderLabels(
            ["REPO", "LAST CHECK", "STATUS", ""]
        )
        self.watches_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.watches_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.watches_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.watches_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.watches_table.verticalHeader().setVisible(False)
        self.watches_table.setAlternatingRowColors(True)
        self.watches_table.setSelectionMode(QTableWidget.NoSelection)
        self.layout.addWidget(self.watches_table)

        # Row 3: recent events feed
        events_label = QLabel(
            f"<span style='color:{NEON['text_muted']}; "
            f"font-family:Menlo; font-size:11px'>RECENT ACTIVITY</span>"
        )
        self.layout.addWidget(events_label)
        self.events_list = QListWidget()
        self.events_list.itemActivated.connect(self._open_event_url)
        self.layout.addWidget(self.events_list, 1)

        self.footer = QLabel()
        self.footer.setStyleSheet(f"color:{NEON['text_muted']}; font-size:11px;")
        self.layout.addWidget(self.footer)

        self.refresh()

    # ---- actions ----------------------------------------------------------
    def _add_repo(self):
        """Add one or many repos from the input box.

        Accepts space- OR comma-separated slugs so paste-lists like
        "owner/a, owner/b, owner/c" all wire up in one action.
        """
        from .github_watch import storage
        raw = self.add_input.text().strip()
        if not raw:
            return
        added, skipped = 0, []
        seen: set[tuple[str, str]] = set()
        for tok in raw.replace(",", " ").split():
            if "/" not in tok:
                skipped.append(tok)
                continue
            owner, _, repo = tok.partition("/")
            owner, repo = owner.strip(), repo.strip()
            if not owner or not repo:
                skipped.append(tok)
                continue
            key = (owner, repo)
            if key in seen:
                continue
            seen.add(key)
            storage.add_watch(owner, repo)
            added += 1
        if added == 0 and skipped:
            QMessageBox.warning(
                self, "Bad format",
                "Repos must look like  owner/repo  (space or comma separated for many)."
            )
            return
        self.add_input.clear()
        self.refresh()
        if skipped:
            QMessageBox.information(
                self, "Some entries skipped",
                f"Added {added}. Skipped (not owner/repo): {', '.join(skipped)}"
            )

    def _poll_now(self):
        """Fire-and-forget: kick the background watcher via a short-lived thread
        so the UI doesn't block on the network call."""
        import threading
        def _bg():
            try:
                from .github_watch import watcher
                watcher.force_poll_all()
            except Exception:
                pass
        threading.Thread(target=_bg, daemon=True).start()
        # Refresh a moment later so results appear.
        QTimer.singleShot(2000, self.refresh)

    def _open_event_url(self, item):
        url = item.data(Qt.UserRole)
        if url:
            import webbrowser
            webbrowser.open(url)

    def _remove_watch(self, owner: str, repo: str):
        from .github_watch import storage
        storage.remove_watch(owner, repo)
        self.refresh()

    # ---- rendering --------------------------------------------------------
    def refresh(self):
        from .github_watch import storage, config as gh_config
        watches = storage.list_watches()
        self.watches_table.setRowCount(len(watches))
        for i, w in enumerate(watches):
            slug = f"{w['owner']}/{w['repo']}"
            self.watches_table.setItem(i, 0, QTableWidgetItem(slug))
            self.watches_table.setItem(
                i, 1, QTableWidgetItem(_fmt_ago(w.get("last_checked")))
            )
            status = w.get("last_error") or ("watching" if w["enabled"] else "paused")
            self.watches_table.setItem(i, 2, QTableWidgetItem(status))
            btn = QPushButton("×")
            btn.setToolTip(f"Stop watching {slug}")
            btn.setCursor(Qt.PointingHandCursor)
            btn.setObjectName("danger")
            btn.setMaximumWidth(28)
            btn.clicked.connect(
                lambda _=False, o=w["owner"], r=w["repo"]: self._remove_watch(o, r)
            )
            self.watches_table.setCellWidget(i, 3, btn)

        events = storage.recent_events(limit=30)
        self.events_list.clear()
        for ev in events:
            slug = f"{ev['owner']}/{ev['repo']}"
            reaction = ev.get("reaction", "curious")
            color = {
                "success": NEON["green"], "error": NEON["red"],
                "curious": NEON["cyan"], "none": NEON["text_muted"],
            }.get(reaction, NEON["text"])
            mark = {"success": "●", "error": "▲", "curious": "◆",
                    "none": "○"}.get(reaction, "•")
            text = f"{mark}  {_fmt_ago(ev['seen_at']):>8}   {slug:<30}  {ev['title']}"
            item = QListWidgetItem(text)
            item.setForeground(QColor(color))
            if ev.get("url"):
                item.setData(Qt.UserRole, ev["url"])
                item.setToolTip("Double-click to open on GitHub")
            self.events_list.addItem(item)

        token_status = "token set" if gh_config.token() else "no token (60 req/hr)"
        interval = gh_config.poll_interval_s()
        self.footer.setText(
            f"Poll interval: {interval}s   ·   {token_status}   ·   "
            f"{len(watches)} watch{'es' if len(watches) != 1 else ''}"
        )


def _fmt_ago(iso: str | None) -> str:
    if not iso:
        return "never"
    try:
        t = datetime.fromisoformat(iso)
        delta = (datetime.now(timezone.utc) - t).total_seconds()
    except Exception:
        return iso
    if delta < 60:
        return f"{int(delta)}s"
    if delta < 3600:
        return f"{int(delta / 60)}m"
    if delta < 86400:
        return f"{int(delta / 3600)}h"
    return f"{int(delta / 86400)}d"


class MemoryPanel(QDialog):
    """The floating panel that opens when you click the pet.

    Futuristic HUD/FUI aesthetic: deep navy background, hairline cyan
    borders, corner brackets, monospace data readouts. Mascot code
    (bot_svg.py) is untouched — this is dashboard-only styling."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Claude Pet — Memory")
        self.setModal(False)
        self.resize(760, 560)
        self.setStyleSheet(_dashboard_stylesheet())

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 16)
        layout.setSpacing(12)

        # Minimal top status strip: pulsing dot + wordmark. No ticker.
        self.status_strip = _StatusStrip(self)
        layout.addWidget(self.status_strip)

        tabs = QTabWidget()
        tabs.setCursor(Qt.PointingHandCursor)
        # Give every tab a solid panel background so switching tabs never
        # leaves the previous view visible (Qt widget transparency bug).
        for tab_cls in (ProjectsTab, GraphTab, SkillsTab, StatsTab, ErgonomicsTab):
            pass  # instantiated below with explicit bg
        self.projects = ProjectsTab()
        self.graph = GraphTab()
        self.skills = SkillsTab()
        self.stats = StatsTab()
        self.ergo = ErgonomicsTab()
        self.github = GithubTab()
        for tab in (self.projects, self.graph, self.skills, self.stats,
                    self.ergo, self.github):
            tab.setAutoFillBackground(True)
            tab.setStyleSheet(
                f"background: {NEON['bg_panel']}; border-radius: 10px;"
            )
        tabs.addTab(self.projects, "PROJECTS")
        tabs.addTab(self.graph, "GRAPH")
        tabs.addTab(self.skills, "SKILLS")
        tabs.addTab(self.stats, "STATS")
        tabs.addTab(self.ergo, "ERGO")
        tabs.addTab(self.github, "GITHUB")
        layout.addWidget(tabs, 1)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        refresh_btn = QPushButton("Refresh")
        refresh_btn.setCursor(Qt.PointingHandCursor)
        refresh_btn.clicked.connect(self._refresh_all)
        close_btn = QPushButton("Close")
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.clicked.connect(self.close)
        actions.addStretch(1)
        actions.addWidget(refresh_btn)
        actions.addWidget(close_btn)
        layout.addLayout(actions)

        # Subtle corner brackets — small, static, no glow.
        self._brackets = _CornerBrackets(self)
        self._brackets.setGeometry(0, 0, self.width(), self.height())
        self._brackets.raise_()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "_brackets"):
            self._brackets.setGeometry(0, 0, self.width(), self.height())

    def _refresh_all(self):
        self.projects.refresh()
        self.graph.refresh()
        self.skills.refresh()
        self.stats.refresh()
        self.ergo.refresh()
        self.github.refresh()
