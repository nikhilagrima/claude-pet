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
    QAction, QBrush, QColor, QFont, QPainter, QPen, QPixmap,
)
from PySide6.QtWidgets import (
    QApplication, QDialog, QGraphicsEllipseItem, QGraphicsLineItem,
    QGraphicsScene, QGraphicsSimpleTextItem, QGraphicsView,
    QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMessageBox,
    QPushButton, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)

from . import memory


TIER_COLOR = {
    "hatchling":  "#3FA3FF",
    "apprentice": "#22D3EE",
    "senior":     "#60A5FA",
    "ponytail":   "#F97316",
}
TIER_ICON = {
    "hatchling":  "🥚",
    "apprentice": "🐣",
    "senior":     "🦉",
    "ponytail":   "🦄",
}


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
        self.delete_btn = QPushButton("Delete selected project from memory…")
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


class StatsTab(QWidget):
    def __init__(self):
        super().__init__()
        self.layout = QVBoxLayout(self)
        self.label = QLabel()
        self.label.setTextFormat(Qt.RichText)
        self.label.setWordWrap(True)
        self.layout.addWidget(self.label)
        self.layout.addStretch(1)
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
        # Rough "tokens saved" estimate: each recalled node ≈ 40 tokens that would
        # otherwise be spent re-reading the file to rediscover it. Conservative floor.
        est_tokens_saved = n_nodes * 40
        html = (
            f"<h2>Claude Pet stats</h2>"
            f"<p><b>Projects tracked:</b> {n_projects}<br>"
            f"<b>Total sessions:</b> {n_sessions}<br>"
            f"<b>Total tool calls:</b> {n_tool_calls}<br>"
            f"<b>Memory graph:</b> {n_nodes} nodes, {n_edges} edges<br>"
            f"<b>Skills learned:</b> {n_skills}<br>"
            f"<b>Highest tier:</b> {TIER_ICON.get(top_tier)} <b>{top_tier}</b></p>"
            f"<hr><p style='color:#94A3B8'>Estimated tokens saved by injection: "
            f"<b>~{est_tokens_saved:,}</b> (assuming ~40 tokens per re-read avoided)</p>"
        )
        self.label.setText(html)


class GraphTab(QWidget):
    """Live animated force-directed graph via QGraphicsScene.

    Real physics: node-node repulsion + edge spring attraction + center gravity,
    stepped ~30 fps by QTimer. If the current project has no nodes yet, we
    render a demo graph so the tab is never dead. Pulsing accent color signals
    the graph is alive."""

    KIND_COLOR = {
        "decision":   "#4ADE80",
        "convention": "#3FA3FF",
        "fix":        "#F97316",
        "gotcha":     "#F87171",
        "file":       "#A78BFA",
        "function":   "#60A5FA",
        "class":      "#22D3EE",
        "module":     "#38BDF8",
        "concept":    "#FCD34D",
        "note":       "#94A3B8",
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
        self.view.setStyleSheet("background: #0A0A1F; border: none;")
        self.view.setDragMode(QGraphicsView.ScrollHandDrag)
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
    """Today's breaks, streak, adherence, most-skipped exercise."""

    def __init__(self):
        super().__init__()
        self.layout = QVBoxLayout(self)
        self.label = QLabel()
        self.label.setTextFormat(Qt.RichText)
        self.label.setWordWrap(True)
        self.layout.addWidget(self.label)
        self.layout.addStretch(1)
        self.refresh()

    def refresh(self):
        from .ergonomics import tracker as t
        adh = t.adherence_last_n_days(7)
        streak = t.daily_streak()
        skipped = t.most_skipped_exercise()
        today = t.today_breaks()
        completed_today = sum(1 for b in today if b["completed"])
        html = "<h2>Ergonomics coach</h2>"
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


class MemoryPanel(QDialog):
    """The floating panel that opens when you click the pet."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Claude Pet — Memory")
        self.setModal(False)
        self.resize(720, 520)
        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        self.projects = ProjectsTab()
        self.graph = GraphTab()
        self.skills = SkillsTab()
        self.stats = StatsTab()
        self.ergo = ErgonomicsTab()
        tabs.addTab(self.projects, "Projects")
        tabs.addTab(self.graph, "Graph")
        tabs.addTab(self.skills, "Skills")
        tabs.addTab(self.stats, "Stats")
        tabs.addTab(self.ergo, "Ergonomics")
        layout.addWidget(tabs)

        actions = QHBoxLayout()
        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self._refresh_all)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        actions.addStretch(1)
        actions.addWidget(refresh_btn)
        actions.addWidget(close_btn)
        layout.addLayout(actions)

    def _refresh_all(self):
        self.projects.refresh()
        self.graph.refresh()
        self.skills.refresh()
        self.stats.refresh()
        self.ergo.refresh()
