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
    QApplication, QCheckBox, QDialog, QGraphicsDropShadowEffect,
    QGraphicsEllipseItem, QGraphicsLineItem, QGraphicsScene,
    QGraphicsSimpleTextItem, QGraphicsView, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
    QPushButton, QScrollArea, QSpinBox, QTableWidget, QTableWidgetItem,
    QTabWidget, QVBoxLayout, QWidget,
)

from . import memory


# ---------- Linear-inspired design tokens ----------
# Retheme to Linear (linear.app): near-black backgrounds, barely-visible
# hairline borders, single purple accent, Inter typography, tight
# compact spacing. Palette values sampled from Linear's design system.
# Dict is still named NEON so downstream references (TIER_COLOR, per-tab
# inline styles) update without a rename sweep — the semantic tokens
# below (bg_deep / border / cyan / etc.) now carry Linear values instead
# of the old HUD/FUI cyan-on-navy ones.
NEON = {
    # Backgrounds — near-black with a subtle blue-purple undertone
    "bg_deep":    "#0A0A0F",     # canvas / dialog root
    "bg_panel":   "#141419",     # tab pane, table body
    "bg_card":    "#1C1C22",     # card, alt row
    "bg_hover":   "#26262E",     # hover / selection
    # Borders — hairline, ~15% white on black; near-invisible until hover
    "border":     "#26262E",
    "border_hi":  "#3A3A44",     # on interactive hover
    # Accent — Linear's signature purple, used sparingly for focus + primary
    "cyan":       "#5E6AD2",     # (name kept for compat; value is now purple)
    "cyan_hi":    "#7A7EEA",     # brighter on hover
    "blue":       "#7A7EEA",     # deprecated slot; alias to cyan_hi
    "magenta":    "#B78AEC",     # soft lavender accent, rare use
    # Semantics — muted, not neon
    "green":      "#4CB782",
    "orange":     "#F2994A",
    "red":        "#EB5757",
    # Text — pure white → grey → muted
    "text":       "#FFFFFF",
    "text_dim":   "#B4BABF",
    "text_muted": "#6E7079",
}

# Linear uses Inter (their custom fork of it, but standard Inter is close).
# Keep the JetBrains Mono option too — some views (Stats readouts, GitHub
# activity feed) still benefit from tabular figures. Downstream code that
# wants a mono font can call _mono_font() explicitly.
def _neon_font() -> str:
    return "Inter, -apple-system, BlinkMacSystemFont, 'SF Pro Text', 'Segoe UI', system-ui, sans-serif"


def _mono_font() -> str:
    return "'SF Mono', 'JetBrains Mono', Menlo, Consolas, monospace"


TIER_COLOR = {
    "hatchling":  NEON["cyan"],
    "apprentice": NEON["blue"],
    "senior":     NEON["magenta"],
    "master":   NEON["orange"],
}
TIER_ICON = {
    "hatchling":  "◇",
    "apprentice": "◆",
    "senior":     "❖",
    "master":   "✦",
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
    """No-op in the Linear theme — Linear's aesthetic is unornamented.
    Kept as a class for API compatibility with MemoryPanel; paints nothing."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_NoSystemBackground)

    def paintEvent(self, event):
        # deliberately empty — Linear doesn't do HUD chrome
        return


class _StatusStrip(QWidget):
    """Linear-style top strip: static solid dot + wordmark + version.
    No pulse, no ticker — Linear's aesthetic is calm, not animated."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(28)

    def paintEvent(self, event):
        from . import __version__
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        # Static solid dot in the accent purple
        p.setBrush(QColor(NEON["cyan"])); p.setPen(Qt.NoPen)
        p.drawEllipse(6, 10, 8, 8)
        # Wordmark — Inter, sentence case (Linear's typography)
        p.setPen(QColor(NEON["text"]))
        f = p.font(); f.setPointSize(12); f.setBold(True)
        f.setFamily("Inter"); p.setFont(f)
        p.drawText(22, 20, "Claude Pet")
        p.setPen(QColor(NEON["text_muted"]))
        f2 = p.font(); f2.setBold(False); f2.setPointSize(11); p.setFont(f2)
        p.drawText(112, 20, f"v{__version__}")


def _dashboard_stylesheet() -> str:
    # Linear-inspired: near-black canvas, hairline borders that appear only
    # on hover, tight radii (6-8px), single purple accent used sparingly on
    # focus + primary actions. Never set QWidget { background: transparent }
    # — that made every tab translucent and prior-tab content bled through.
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

    /* Tabs — Linear pattern: no chrome per tab; active state is a subtle
       background lift + a 1px accent underline (fake via border-bottom). */
    QTabWidget::pane {{
        border: 1px solid {NEON['border']};
        border-radius: 8px;
        background: {NEON['bg_panel']};
        top: -1px;
    }}
    QTabBar::tab {{
        background: transparent;
        color: {NEON['text_muted']};
        padding: 8px 14px;
        margin-right: 2px;
        border: 1px solid transparent;
        border-radius: 6px;
        font-weight: 500;
        font-family: {_neon_font()};
        letter-spacing: 0;
        font-size: 12px;
    }}
    QTabBar::tab:hover {{
        color: {NEON['text']};
        background: {NEON['bg_card']};
    }}
    QTabBar::tab:selected {{
        color: {NEON['text']};
        background: {NEON['bg_hover']};
        border: 1px solid {NEON['border']};
    }}

    /* Buttons — Linear's signature look: subtle card bg, no bold border,
       purple accent only on primary + focus. No hover glow. */
    QPushButton {{
        background: {NEON['bg_card']};
        color: {NEON['text']};
        border: 1px solid {NEON['border']};
        padding: 6px 14px;
        border-radius: 6px;
        font-weight: 500;
        font-family: {_neon_font()};
        letter-spacing: 0;
        font-size: 13px;
        min-height: 22px;
    }}
    QPushButton:hover {{
        background: {NEON['bg_hover']};
        border: 1px solid {NEON['border_hi']};
        color: {NEON['text']};
    }}
    QPushButton:pressed {{
        background: {NEON['bg_panel']};
    }}
    QPushButton:disabled {{
        color: {NEON['text_muted']};
        border-color: {NEON['border']};
        background: {NEON['bg_panel']};
    }}
    QPushButton#danger {{
        color: {NEON['red']};
    }}
    QPushButton#danger:hover {{
        background: rgba(235, 87, 87, 0.10);
        border-color: {NEON['red']};
    }}
    QPushButton#primary {{
        background: {NEON['cyan']};
        border: 1px solid {NEON['cyan']};
        color: #FFFFFF;
        font-weight: 600;
    }}
    QPushButton#primary:hover {{
        background: {NEON['cyan_hi']};
        border-color: {NEON['cyan_hi']};
    }}

    /* Tables — Linear list style: no gridlines, subtle row separators,
       hover row background instead of neon selection. */
    QTableWidget {{
        background: {NEON['bg_panel']};
        border: 1px solid {NEON['border']};
        border-radius: 8px;
        gridline-color: transparent;
        color: {NEON['text']};
        alternate-background-color: {NEON['bg_panel']};
        font-family: {_neon_font()};
        selection-background-color: {NEON['bg_hover']};
        selection-color: {NEON['text']};
    }}
    QHeaderView::section {{
        background: {NEON['bg_panel']};
        color: {NEON['text_muted']};
        border: none;
        border-bottom: 1px solid {NEON['border']};
        padding: 8px 10px;
        font-weight: 500;
        text-transform: none;
        letter-spacing: 0;
        font-size: 11px;
    }}
    QTableWidget::item {{
        padding: 8px 10px;
        border-bottom: 1px solid {NEON['border']};
    }}
    QTableWidget::item:selected {{
        background: {NEON['bg_hover']};
        color: {NEON['text']};
    }}

    /* Lists — Linear inbox-style rows */
    QListWidget {{
        background: {NEON['bg_panel']};
        border: 1px solid {NEON['border']};
        border-radius: 8px;
        color: {NEON['text']};
        padding: 4px;
        font-family: {_neon_font()};
    }}
    QListWidget::item {{
        background: transparent;
        border: none;
        border-radius: 6px;
        padding: 8px 12px;
        margin: 4px 2px;
    }}
    QListWidget::item:hover {{
        background: {NEON['bg_card']};
    }}
    QListWidget::item:selected {{
        background: {NEON['bg_hover']};
        color: {NEON['text']};
    }}

    /* Text inputs — Linear's field style: minimal border at rest, purple
       accent only on focus. Subtle background lift on focus signals edit. */
    QLineEdit {{
        background: {NEON['bg_card']};
        color: {NEON['text']};
        border: 1px solid {NEON['border']};
        border-radius: 6px;
        padding: 7px 10px;
        font-family: {_neon_font()};
        font-size: 13px;
        selection-background-color: {NEON['cyan']};
        selection-color: #FFFFFF;
        min-height: 20px;
        placeholder-text-color: {NEON['text_muted']};
    }}
    QLineEdit:hover {{
        border: 1px solid {NEON['border_hi']};
    }}
    QLineEdit:focus {{
        border: 1px solid {NEON['cyan']};
        background: {NEON['bg_hover']};
    }}
    QLineEdit:disabled {{
        color: {NEON['text_muted']};
        border-color: {NEON['border']};
        background: {NEON['bg_panel']};
    }}

    /* Numeric spinboxes — matching field style */
    QSpinBox, QDoubleSpinBox {{
        background: {NEON['bg_card']};
        color: {NEON['text']};
        border: 1px solid {NEON['border']};
        border-radius: 6px;
        padding: 6px 8px;
        font-family: {_neon_font()};
        font-size: 13px;
        min-height: 20px;
    }}
    QSpinBox:hover, QDoubleSpinBox:hover {{
        border: 1px solid {NEON['border_hi']};
    }}
    QSpinBox:focus, QDoubleSpinBox:focus {{
        border: 1px solid {NEON['cyan']};
        background: {NEON['bg_hover']};
    }}
    QSpinBox::up-button, QSpinBox::down-button,
    QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
        background: transparent;
        border: none;
        width: 16px;
    }}
    QSpinBox::up-button:hover, QSpinBox::down-button:hover,
    QDoubleSpinBox::up-button:hover, QDoubleSpinBox::down-button:hover {{
        background: {NEON['bg_hover']};
    }}
    QSpinBox::up-arrow, QDoubleSpinBox::up-arrow,
    QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{
        width: 8px; height: 8px;
    }}

    /* Checkboxes — Linear's small square indicator, filled purple when on */
    QCheckBox {{
        color: {NEON['text']};
        font-family: {_neon_font()};
        font-size: 13px;
        spacing: 8px;
        padding: 3px 0;
    }}
    QCheckBox:disabled {{
        color: {NEON['text_muted']};
    }}
    QCheckBox::indicator {{
        width: 16px;
        height: 16px;
        border: 1px solid {NEON['border_hi']};
        border-radius: 4px;
        background: {NEON['bg_card']};
    }}
    QCheckBox::indicator:hover {{
        border: 1px solid {NEON['cyan']};
    }}
    QCheckBox::indicator:checked {{
        background: {NEON['cyan']};
        border: 1px solid {NEON['cyan']};
        image: none;
    }}
    QCheckBox::indicator:checked:hover {{
        background: {NEON['cyan_hi']};
        border: 1px solid {NEON['cyan_hi']};
    }}
    QCheckBox::indicator:disabled {{
        border-color: {NEON['border']};
        background: {NEON['bg_panel']};
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
        # Row-level right-click context menu → makes per-row delete obvious
        # instead of the users-must-select-first + click-button pattern
        # that read as "nothing happened" for many users.
        from PySide6.QtWidgets import QMenu, QAbstractItemView
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_row_menu)
        # Double-click a row to trigger delete too (with confirm).
        self.table.doubleClicked.connect(self._on_double_click)
        layout.addWidget(self.table, 1)

        # Hint above the button — makes the workflow discoverable.
        hint = QLabel(
            f"<span style='color:{NEON['text_muted']}; font-size:11px'>"
            f"Select a row, then click Delete — or right-click a row for "
            f"quick actions."
            f"</span>"
        )
        layout.addWidget(hint)

        button_row = QHBoxLayout()
        self.delete_btn = QPushButton("⚠  DELETE SELECTED PROJECT FROM MEMORY")
        self.delete_btn.setObjectName("danger")
        self.delete_btn.setCursor(Qt.PointingHandCursor)
        self.delete_btn.setToolTip(
            "Click a row above first, then this button. "
            "Right-click a row for a per-row Delete option."
        )
        self.delete_btn.clicked.connect(self._delete_selected)
        button_row.addWidget(self.delete_btn)
        button_row.addStretch(1)
        layout.addLayout(button_row)

        self._path_by_row: dict[int, str] = {}
        self.refresh()

    def _show_row_menu(self, pos):
        """Right-click a row → show a Delete option scoped to THAT row."""
        from PySide6.QtWidgets import QMenu
        item = self.table.itemAt(pos)
        if item is None:
            return
        row = item.row()
        if row not in self._path_by_row:
            return
        # Ensure the clicked row is also visually selected so the state is
        # consistent if the user afterwards clicks the main Delete button.
        self.table.selectRow(row)
        path = self._path_by_row[row]
        menu = QMenu(self)
        act_del = menu.addAction(f"Delete “{path}” from memory")
        chosen = menu.exec(self.table.viewport().mapToGlobal(pos))
        if chosen == act_del:
            self._delete_project(path)

    def _on_double_click(self, index):
        row = index.row()
        if row in self._path_by_row:
            self.table.selectRow(row)
            self._delete_project(self._path_by_row[row])

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
        """Big-button path — requires a row already selected."""
        from .errors import log_warning
        row = self.table.currentRow()
        log_warning("panel.ProjectsTab.delete_btn",
                    f"click, currentRow={row}, rowCount={self.table.rowCount()}")
        if row < 0 or row not in self._path_by_row:
            QMessageBox.information(
                self, "Pick a row first",
                "Click a project row in the table above, then click Delete "
                "again. (Or right-click any row → Delete for a shortcut.)"
            )
            return
        self._delete_project(self._path_by_row[row])

    def _delete_project(self, path: str):
        """Confirm + delete flow, shared by button / right-click / double-click."""
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
        try:
            counts = memory.delete_project(path)
        except Exception as exc:
            from .errors import log_exception
            log_exception("panel.ProjectsTab._delete_project")
            QMessageBox.critical(
                self, "Delete failed",
                f"Could not delete {path}:\n\n{exc}\n\n"
                "See ~/.claude/claude-pet/errors.log for details."
            )
            return
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

        # Token-savings estimate — measure a real context block instead of
        # guessing 40/node. Each SessionStart injects a block up to 800 tokens;
        # sessions after the FIRST for each project benefit (the first has no
        # prior memory to inject). Correct formula: per-project max(0, count-1)
        # summed — NOT global (n_sessions - n_projects), which is wrong when
        # some projects have 0 sessions (auto-registered by a hook that never
        # produced a real session).
        per_block_tokens = 0
        projects = memory.list_projects(limit=1)
        if projects:
            try:
                from . import context as _ctx
                blk = _ctx.build_context(projects[0]["path"], token_budget=800)
                per_block_tokens = max(0, len(blk) // 4)
            except Exception:
                per_block_tokens = 0
        with memory.connect() as conn:
            per_project_counts = [
                r["session_count"] for r in conn.execute(
                    "SELECT session_count FROM projects"
                ).fetchall()
            ]
        sessions_with_memory = sum(max(0, c - 1) for c in per_project_counts)
        est_tokens_saved = sessions_with_memory * per_block_tokens + n_nodes * 40
        # Gauge cap raised — real accumulations can hit hundreds of k. Log-ish
        # scale so the ring reads meaningfully even at 500k+.
        saved_pct = min(1.0, math.log10(max(est_tokens_saved, 1)) / 6.0)

        # Graph density: edges relative to max possible n*(n-1)/2.
        density = 0.0
        if n_nodes > 1:
            density = min(1.0, n_edges / (n_nodes * (n_nodes - 1) / 2))

        self.cell_projects.set_value(str(n_projects))
        self.cell_sessions.set_value(str(n_sessions))
        self.cell_tools.set_value(str(n_tool_calls))
        self.cell_skills.set_value(str(n_skills), f"tier: {top_tier}")

        # Compact gauge label: 1.2k / 45k / 320k etc.
        def _short(n: int) -> str:
            if n >= 1_000_000:
                return f"{n / 1_000_000:.1f}M"
            if n >= 1_000:
                return f"{n / 1_000:.1f}k".replace(".0k", "k")
            return str(n)
        self.gauge_saved.set_data(saved_pct, _short(est_tokens_saved))
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
            f"<span style='color:{NEON['cyan']}; font-weight:700'>TOKEN SAVINGS</span><br>"
            f"~<b style='color:{NEON['text']}'>{est_tokens_saved:,}</b> tokens saved cumulative<br>"
            f"<span style='color:{NEON['text_muted']}'>"
            f"{sessions_with_memory} sessions × {per_block_tokens} tok injected each"
            f"</span></p>"
            f"</div>"
        )


class _GraphView(QGraphicsView):
    """QGraphicsView that reports clicks back to its owning GraphTab.

    We can't put click handling on the scene items because the animation
    layer replaces their rects every frame — cleaner to intercept at the view
    level, translate to scene coords, and let the tab match against known
    node positions.
    """

    def __init__(self, tab: "GraphTab"):
        super().__init__()
        self._tab = tab

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            scene_pt = self.mapToScene(event.position().toPoint())
            self._tab._on_scene_click(scene_pt.x(), scene_pt.y())
        super().mousePressEvent(event)


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
            "Weight = size, kind = color. Click a node for details."
        )
        self.info.setWordWrap(True)
        layout.addWidget(self.info)

        # Split: scene on the left, details panel on the right.
        split_row = QHBoxLayout()
        split_row.setSpacing(10)
        self.view = _GraphView(self)
        self.view.setRenderHint(QPainter.Antialiasing)
        self.view.setStyleSheet(
            f"background: {NEON['bg_deep']}; "
            f"border: 1px solid {NEON['border']}; border-radius: 12px;"
        )
        self.view.setDragMode(QGraphicsView.ScrollHandDrag)
        self.view.setCursor(Qt.OpenHandCursor)
        split_row.addWidget(self.view, 2)

        # Details side-panel: shows the clicked node's kind/value/reinforcements
        # + list of neighbors. Empty state prompts the user to click.
        self.details = QLabel(
            "<p style='color:#5A7099; font-size:12px; font-family:Menlo'>"
            "CLICK A NODE<br><br>"
            "Nothing selected. Click any dot in the graph to see its details, "
            "reinforcement count, and connections."
            "</p>"
        )
        self.details.setTextFormat(Qt.RichText)
        self.details.setWordWrap(True)
        self.details.setAlignment(Qt.AlignTop)
        self.details.setStyleSheet(
            f"background: {NEON['bg_card']}; "
            f"border: 1px solid {NEON['border']}; border-radius: 12px; "
            f"padding: 14px; color: {NEON['text']};"
        )
        self.details.setMinimumWidth(220)
        self.details.setMaximumWidth(320)
        split_row.addWidget(self.details, 1)

        layout.addLayout(split_row, 1)

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

    # -------------------------------------------------------------- interaction
    def _on_scene_click(self, x: float, y: float):
        """Find the nearest node within its radius and populate details."""
        best_id, best_dist = None, 1e9
        for nid, (_item, base_r, _color) in self._items.items():
            p = self._positions.get(nid)
            if not p:
                continue
            dx, dy = x - p[0], y - p[1]
            d = (dx * dx + dy * dy) ** 0.5
            # Add a small padding so tiny nodes are still clickable.
            if d <= max(base_r, 10) and d < best_dist:
                best_id, best_dist = nid, d
        if best_id is None:
            return
        node = next((n for n in self._nodes if n["id"] == best_id), None)
        if not node:
            return
        self._render_details(node)

    def _render_details(self, node: dict):
        kind = node.get("kind", "?")
        color = self.KIND_COLOR.get(kind, NEON["text"])
        value = str(node.get("value", "")).strip() or "(no value)"
        # Neighbors: any edge touching this node in the current graph.
        neighbors: list[tuple[str, str]] = []
        for e in self._edges:
            other_id = None
            if e.get("src_id") == node["id"]:
                other_id = e.get("dst_id")
            elif e.get("dst_id") == node["id"]:
                other_id = e.get("src_id")
            if other_id is None:
                continue
            other = next((n for n in self._nodes if n["id"] == other_id), None)
            if not other:
                continue
            snippet = str(other.get("value", ""))[:70]
            neighbors.append((str(e.get("kind") or "related"), snippet))

        html = []
        html.append(
            f"<div style='font-family:Menlo; font-size:10px; letter-spacing:1.5px; "
            f"text-transform:uppercase; color:{color}'>{kind}</div>"
        )
        html.append(
            f"<div style='margin-top:6px; font-size:13px; color:{NEON['text']}; "
            f"line-height:1.45; word-break:break-word'>{_escape(value)}</div>"
        )
        if not self._is_demo:
            html.append(
                f"<hr style='border:0; border-top:1px solid {NEON['border']}; margin:12px 0'/>"
            )
            html.append(
                f"<div style='font-family:Menlo; font-size:11px; color:{NEON['text_muted']}'>"
                f"weight <b style='color:{NEON['cyan']}'>{node.get('weight', 0):.1f}</b>"
                f" · reinforced <b style='color:{NEON['cyan']}'>"
                f"{node.get('reinforcements', 1)}×</b>"
                f"</div>"
            )
            if node.get("file_path"):
                html.append(
                    f"<div style='margin-top:4px; font-family:Menlo; "
                    f"font-size:11px; color:{NEON['text_muted']}; word-break:break-all'>"
                    f"file: {_escape(node['file_path'])}</div>"
                )
            if node.get("last_seen"):
                html.append(
                    f"<div style='margin-top:4px; font-family:Menlo; "
                    f"font-size:11px; color:{NEON['text_muted']}'>"
                    f"last seen: {_escape(node['last_seen'])}</div>"
                )
        html.append(
            f"<hr style='border:0; border-top:1px solid {NEON['border']}; margin:12px 0'/>"
        )
        html.append(
            f"<div style='font-family:Menlo; font-size:10px; letter-spacing:1.5px; "
            f"text-transform:uppercase; color:{NEON['text_muted']}'>"
            f"{len(neighbors)} connection{'s' if len(neighbors) != 1 else ''}</div>"
        )
        if neighbors:
            for kind_, snip in neighbors[:8]:
                html.append(
                    f"<div style='margin-top:6px; font-size:12px; color:{NEON['text_dim']}'>"
                    f"<span style='color:{NEON['cyan']}; font-family:Menlo; font-size:10px'>"
                    f"{kind_}</span> &nbsp; {_escape(snip)}</div>"
                )
        else:
            html.append(
                f"<div style='margin-top:6px; font-size:11px; color:{NEON['text_muted']}'>"
                "No connections yet. Nodes gain edges when they appear together in the same session."
                "</div>"
            )
        self.details.setText("".join(html))

    def showEvent(self, event):
        super().showEvent(event)
        if self._timer and not self._timer.isActive():
            self._timer.start(self.TICK_MS)

    def hideEvent(self, event):
        super().hideEvent(event)
        # Stop physics when tab is off-screen — no CPU when not visible.
        if self._timer and self._timer.isActive():
            self._timer.stop()


def _escape(text: str) -> str:
    """Minimal HTML escape for QLabel rich-text content."""
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


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
        from pathlib import Path as _P
        tok = ""
        try:
            _tp = _P.home() / ".claude" / "claude-pet" / "server.token"
            if _tp.exists():
                tok = _tp.read_text().strip()
        except Exception:
            pass
        try:
            _u.urlopen(_u.Request(
                "http://localhost:5050/break",
                data=_json.dumps({}).encode(),
                headers={"Content-Type": "application/json",
                         "X-Pet-Token": tok},
                method="POST",
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

        # ── Add-repo panel ── labeled, highlighted, obviously interactive.
        # Users kept missing this input entirely — now we frame it as a call
        # to action with a header, a hint line, cyan focus glow.
        add_panel = QWidget()
        add_panel.setObjectName("addPanel")
        add_panel.setStyleSheet(
            f"#addPanel {{ "
            f"background: {NEON['bg_card']}; "
            f"border: 1px solid {NEON['border_hi']}; "
            f"border-radius: 10px; padding: 10px; "
            f"}}"
        )
        add_wrap = QVBoxLayout(add_panel)
        add_wrap.setContentsMargins(12, 10, 12, 12)
        add_wrap.setSpacing(6)

        # Header: makes it obvious this box is where you add things.
        header = QLabel(
            f"<span style='color:{NEON['cyan']}; font-family:Menlo; "
            f"font-size:11px; letter-spacing:1.5px'>▸ ADD A REPO TO WATCH</span>"
        )
        add_wrap.addWidget(header)

        # Input + buttons row.
        add_row = QHBoxLayout()
        add_row.setSpacing(8)
        self.add_input = QLineEdit()
        self.add_input.setPlaceholderText("owner/repo   e.g.  facebook/react")
        self.add_input.setClearButtonEnabled(True)
        # Height only; visual styling is inherited from the global stylesheet
        # so every input on every tab looks the same (focus ring, focus glow).
        self.add_input.setMinimumHeight(34)
        self.add_input.returnPressed.connect(self._add_repo)
        add_row.addWidget(self.add_input, 1)

        self.add_btn = QPushButton("+ Watch")
        self.add_btn.setObjectName("primary")
        self.add_btn.setCursor(Qt.PointingHandCursor)
        self.add_btn.setMinimumHeight(34)
        self.add_btn.clicked.connect(self._add_repo)
        add_row.addWidget(self.add_btn)

        self.check_btn = QPushButton("Poll now")
        self.check_btn.setCursor(Qt.PointingHandCursor)
        self.check_btn.setMinimumHeight(34)
        self.check_btn.clicked.connect(self._poll_now)
        add_row.addWidget(self.check_btn)
        add_wrap.addLayout(add_row)

        # Hint line — tells you multi-add works without cluttering the placeholder.
        hint = QLabel(
            f"<span style='color:{NEON['text_muted']}; font-size:11px'>"
            f"Type the GitHub slug and press <b>Enter</b>. "
            f"Multiple ok — separate with a space or comma."
            f"</span>"
        )
        hint.setWordWrap(True)
        add_wrap.addWidget(hint)

        self.layout.addWidget(add_panel)

        # Auto-focus the input when the tab opens so people see the cursor
        # blinking on the field they're meant to use.
        QTimer.singleShot(50, self.add_input.setFocus)

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
        from .errors import log_warning, log_exception
        log_warning("panel.GithubTab.remove_btn", f"click {owner}/{repo}")
        confirm = QMessageBox.warning(
            self, "Stop watching?",
            f"Remove the watch for {owner}/{repo}?\n\n"
            "Watch cursor + local event history for this repo are deleted. "
            "You can re-add the repo any time.",
            QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel,
        )
        if confirm != QMessageBox.Yes:
            return
        try:
            ok = storage.remove_watch(owner, repo)
        except Exception as exc:
            log_exception("panel.GithubTab._remove_watch")
            QMessageBox.critical(
                self, "Remove failed",
                f"Could not stop watching {owner}/{repo}:\n\n{exc}"
            )
            return
        if not ok:
            QMessageBox.information(
                self, "Not watched",
                f"{owner}/{repo} wasn't in the watch list.",
            )
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
            btn = QPushButton("Remove")
            btn.setToolTip(f"Stop watching {slug}")
            btn.setCursor(Qt.PointingHandCursor)
            btn.setObjectName("danger")
            btn.setMinimumWidth(80)
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


class SettingsTab(QWidget):
    """Everything that used to require hand-editing ~/.claude/claude-pet/config.json.

    Layout: single scrollable column of collapsible-ish sections.
    - Ergonomics: master toggle, quiet hours, per-category interval + enabled
    - GitHub: enabled, poll interval, token (masked), per-event-type filter
    - Debug: link to error log path + last mtime + size

    Every change writes immediately (no Save button) — matches macOS
    System Settings pattern users already know.
    """

    def __init__(self):
        super().__init__()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        container = QWidget()
        container.setStyleSheet(f"background: {NEON['bg_panel']};")
        self.layout = QVBoxLayout(container)
        self.layout.setContentsMargins(14, 14, 14, 14)
        self.layout.setSpacing(16)

        self._build_pet_section()
        self._build_ergonomics_section()
        self._build_github_section()
        self._build_debug_section()
        self.layout.addStretch(1)

        scroll.setWidget(container)
        outer.addWidget(scroll)
        self.refresh()

    # ---- section builders --------------------------------------------------
    def _header(self, text: str, color=None) -> QLabel:
        color = color or NEON["cyan"]
        h = QLabel(
            f"<span style='color:{color}; font-family:Menlo; font-size:11px; "
            f"letter-spacing:1.5px; font-weight:700'>▸ {text}</span>"
        )
        return h

    def _build_pet_section(self):
        self.layout.addWidget(self._header("PET"))
        self.mute_checkbox = QCheckBox(
            "Mute all sounds (breaks, GitHub alerts, Claude Code events)"
        )
        self.mute_checkbox.setToolTip(
            "Silence every sound the pet plays. Emotions + toasts still show."
        )
        self.mute_checkbox.stateChanged.connect(self._save_pet)
        self.layout.addWidget(self.mute_checkbox)

    def _build_ergonomics_section(self):
        self.layout.addWidget(self._header("ERGONOMICS COACH"))
        self.ergo_enabled = QCheckBox("Ergonomics coach enabled")
        self.ergo_enabled.stateChanged.connect(self._save_ergonomics)
        self.layout.addWidget(self.ergo_enabled)

        self.ergo_quiet = QCheckBox("Quiet hours enabled")
        self.ergo_quiet.stateChanged.connect(self._save_ergonomics)
        self.layout.addWidget(self.ergo_quiet)

        self.ergo_categories = {}
        self.ergo_minutes = {}
        for cat in ("eyes", "neck", "wrists", "posture", "hydration"):
            row = QHBoxLayout()
            cb = QCheckBox(cat.title())
            cb.stateChanged.connect(self._save_ergonomics)
            row.addWidget(cb, 1)
            sb = QSpinBox()
            sb.setRange(1, 240)
            sb.setSuffix(" min")
            sb.setFixedWidth(90)
            sb.valueChanged.connect(self._save_ergonomics)
            row.addWidget(sb)
            self.layout.addLayout(row)
            self.ergo_categories[cat] = cb
            self.ergo_minutes[cat] = sb

    def _build_github_section(self):
        self.layout.addWidget(self._header("GITHUB WATCHER"))
        self.gh_enabled = QCheckBox("GitHub watcher enabled")
        self.gh_enabled.stateChanged.connect(self._save_github)
        self.layout.addWidget(self.gh_enabled)

        interval_row = QHBoxLayout()
        interval_row.addWidget(QLabel("Poll interval:"))
        self.gh_interval = QSpinBox()
        self.gh_interval.setRange(60, 3600)
        self.gh_interval.setSuffix(" s")
        self.gh_interval.setFixedWidth(120)
        self.gh_interval.valueChanged.connect(self._save_github)
        interval_row.addWidget(self.gh_interval)
        interval_row.addStretch(1)
        self.layout.addLayout(interval_row)

        tok_row = QHBoxLayout()
        tok_row.addWidget(QLabel("GitHub token (optional):"))
        self.gh_token_input = QLineEdit()
        self.gh_token_input.setEchoMode(QLineEdit.Password)
        self.gh_token_input.setPlaceholderText("ghp_… or github_pat_…")
        self.gh_token_input.editingFinished.connect(self._save_github)
        tok_row.addWidget(self.gh_token_input, 1)
        self.layout.addLayout(tok_row)

        self.layout.addWidget(QLabel(
            f"<span style='color:{NEON['text_muted']}; font-size:11px'>"
            f"Which events fire the pet:</span>"
        ))
        self.gh_alerts = {}
        for et in ("PushEvent", "PullRequestEvent", "PullRequestReviewEvent",
                   "ReleaseEvent", "IssuesEvent", "WorkflowRunEvent",
                   "DeploymentStatusEvent"):
            cb = QCheckBox(et)
            cb.stateChanged.connect(self._save_github)
            self.layout.addWidget(cb)
            self.gh_alerts[et] = cb

    def _build_debug_section(self):
        self.layout.addWidget(self._header("DEBUG", color=NEON["text_muted"]))
        self.debug_label = QLabel()
        self.debug_label.setTextFormat(Qt.RichText)
        self.debug_label.setWordWrap(True)
        self.debug_label.setStyleSheet(
            f"color:{NEON['text_dim']}; font-family:Menlo; font-size:11px;"
        )
        self.layout.addWidget(self.debug_label)

    # ---- persistence -------------------------------------------------------
    def _save_pet(self, *_):
        if not getattr(self, "_ready", False):
            return
        from . import pet_config
        pet_config.set_muted(self.mute_checkbox.isChecked())

    def _save_ergonomics(self, *_):
        if not getattr(self, "_ready", False):
            return       # ignore initial refresh() setValue events
        from .ergonomics import config as ergo_cfg
        cfg = ergo_cfg.load()
        cfg["enabled"] = self.ergo_enabled.isChecked()
        qh = cfg.get("quiet_hours", {})
        qh["enabled"] = self.ergo_quiet.isChecked()
        cfg["quiet_hours"] = qh
        cats = cfg.get("categories_enabled", {})
        mins = cfg.get("intervals_min", {})
        for cat, cb in self.ergo_categories.items():
            cats[cat] = cb.isChecked()
            mins[cat] = self.ergo_minutes[cat].value()
        cfg["categories_enabled"] = cats
        cfg["intervals_min"] = mins
        ergo_cfg.save(cfg)

    def _save_github(self, *_):
        if not getattr(self, "_ready", False):
            return
        from .github_watch import config as gh_cfg
        cfg = gh_cfg.load()
        cfg["enabled"] = self.gh_enabled.isChecked()
        cfg["poll_interval_s"] = self.gh_interval.value()
        # Only persist the token if the user actually typed one; blank
        # means "don't change" (avoids wiping a real token on refocus).
        typed = self.gh_token_input.text().strip()
        if typed:
            cfg["token"] = typed
        alerts = cfg.get("alert_types", {})
        for et, cb in self.gh_alerts.items():
            alerts[et] = cb.isChecked()
        cfg["alert_types"] = alerts
        gh_cfg.save(cfg)

    # ---- refresh from disk -------------------------------------------------
    def refresh(self):
        from .ergonomics import config as ergo_cfg
        from .github_watch import config as gh_cfg
        from . import pet_config
        self._ready = False
        try:
            self.mute_checkbox.setChecked(bool(pet_config.is_muted()))

            ec = ergo_cfg.load()
            self.ergo_enabled.setChecked(bool(ec.get("enabled", True)))
            self.ergo_quiet.setChecked(bool(ec.get("quiet_hours", {}).get("enabled")))
            cats = ec.get("categories_enabled", {})
            mins = ec.get("intervals_min", {})
            for cat, cb in self.ergo_categories.items():
                cb.setChecked(bool(cats.get(cat, True)))
                self.ergo_minutes[cat].setValue(int(mins.get(cat, 20)))

            gc = gh_cfg.load()
            self.gh_enabled.setChecked(bool(gc.get("enabled", True)))
            self.gh_interval.setValue(int(gc.get("poll_interval_s", 300)))
            # Don't populate the token field — leave it blank so nothing
            # accidentally leaks on screen. Placeholder tells the user.
            has_tok = bool(gc.get("token"))
            self.gh_token_input.setPlaceholderText(
                "token set (leave blank to keep)" if has_tok
                else "ghp_… or github_pat_… (optional)"
            )
            alerts = gc.get("alert_types", {})
            for et, cb in self.gh_alerts.items():
                cb.setChecked(bool(alerts.get(et, True)))

            # Debug section — error-log status.
            from . import errors as _errors
            elog = _errors.log_path()
            if elog.exists():
                size = elog.stat().st_size
                self.debug_label.setText(
                    f"Error log: <code>{_escape_html(str(elog))}</code><br>"
                    f"Size: {size} bytes · "
                    f"Empty means no silent failures recorded."
                )
            else:
                self.debug_label.setText(
                    f"Error log: <code>{_escape_html(str(elog))}</code><br>"
                    f"Not yet created — pet has run clean."
                )
        finally:
            self._ready = True


def _escape_html(text: str) -> str:
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


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
        self.settings = SettingsTab()
        for tab in (self.projects, self.graph, self.skills, self.stats,
                    self.ergo, self.github, self.settings):
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
        tabs.addTab(self.settings, "SETTINGS")
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
        self.settings.refresh()
