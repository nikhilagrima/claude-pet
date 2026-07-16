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
    QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton,
    QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
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
        layout.addWidget(self.table)
        self.refresh()

    def refresh(self):
        rows = memory.list_projects(limit=200)
        self.table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            self.table.setItem(i, 0, QTableWidgetItem(r["name"] + "  —  " + r["path"]))
            self.table.setItem(i, 1, QTableWidgetItem(str(r["session_count"])))
            self.table.setItem(i, 2, QTableWidgetItem(str(r["tool_calls"])))
            self.table.setItem(i, 3, QTableWidgetItem(r["last_seen"]))
        self.table.resizeColumnsToContents()


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
    """Force-layout graph via QGraphicsScene — self-contained, no HTML/webengine."""

    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        self.info = QLabel("Nodes from your most-recent project. Weight = size, kind = color.")
        self.info.setWordWrap(True)
        layout.addWidget(self.info)
        self.view = QGraphicsView()
        self.view.setRenderHint(QPainter.Antialiasing)
        self.view.setStyleSheet("background: #0A0A1F;")
        layout.addWidget(self.view, 1)
        self.refresh()

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
    }

    def refresh(self):
        scene = QGraphicsScene()
        scene.setSceneRect(-350, -250, 700, 500)

        # Pick the most-recent project for now.
        projects = memory.list_projects(limit=1)
        if not projects:
            scene.addText("No project data yet.", QFont("Helvetica", 12)).setDefaultTextColor(Qt.white)
            self.view.setScene(scene)
            return
        pp = projects[0]["path"]
        nodes = memory.top_nodes(pp, limit=40)
        if not nodes:
            scene.addText("Empty graph.", QFont("Helvetica", 12)).setDefaultTextColor(Qt.white)
            self.view.setScene(scene)
            return

        with memory.connect() as conn:
            edges = conn.execute(
                "SELECT src_id, dst_id, kind FROM edges WHERE project_path = ?", (pp,)
            ).fetchall()

        # Circular layout (stable, deterministic; no springs to converge).
        R = 200
        pos = {}
        n = len(nodes)
        for i, node in enumerate(nodes):
            ang = 2 * math.pi * i / n
            pos[node["id"]] = QPointF(R * math.cos(ang), R * math.sin(ang))

        # Draw edges first (under nodes).
        edge_pen = QPen(QColor("#334155"))
        edge_pen.setWidthF(0.8)
        for e in edges:
            src = pos.get(e["src_id"])
            dst = pos.get(e["dst_id"])
            if src and dst:
                line = QGraphicsLineItem(src.x(), src.y(), dst.x(), dst.y())
                line.setPen(edge_pen)
                scene.addItem(line)

        # Draw nodes.
        for node in nodes:
            p = pos[node["id"]]
            r = 6 + 3 * math.log2(max(node["weight"], 1))
            r = min(r, 20)
            color = QColor(self.KIND_COLOR.get(node["kind"], "#94A3B8"))
            ell = QGraphicsEllipseItem(p.x() - r, p.y() - r, r * 2, r * 2)
            ell.setBrush(QBrush(color))
            ell.setPen(QPen(QColor("#0A0A1F"), 1))
            ell.setToolTip(f"[{node['kind']}] {node['value']}\nweight={node['weight']:.1f}, "
                           f"reinforced={node['reinforcements']}×")
            scene.addItem(ell)

        self.view.setScene(scene)
        self.view.fitInView(scene.sceneRect(), Qt.KeepAspectRatio)


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
        tabs.addTab(self.projects, "Projects")
        tabs.addTab(self.graph, "Graph")
        tabs.addTab(self.skills, "Skills")
        tabs.addTab(self.stats, "Stats")
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
