"""Session distiller — turn the tool-call trace of a just-ended session into
persistent graph nodes (decisions, conventions, fixes, gotchas).

Rule-based, 100% local, network-free. Uses only the tool_usage + notes rows
we already record. Optional `claude -p --model claude-haiku-4-5` upgrade path
if the CLI is on PATH — never required.

The distiller is deliberately conservative: it only writes a node when it's
seen genuine evidence (repeated pattern, explicit note, error-followed-by-fix).
False positives are worse than false negatives — an over-eager pet floods
Claude's context with noise and defeats the whole purpose.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from typing import Iterable

from . import memory


# ---------------------------------------------------------------------------
# Secret redaction — runs on every value BEFORE it hits SQLite.
# ---------------------------------------------------------------------------

# Patterns tuned to be broad-but-precise. We prefer redacting benign strings
# over ever letting a real key through. Order matters — longer/specific first.
_SECRET_PATTERNS: tuple[tuple[str, re.Pattern], ...] = (
    ("aws", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("github-classic", re.compile(r"ghp_[A-Za-z0-9]{20,}")),
    ("github-fine", re.compile(r"github_pat_[A-Za-z0-9_]{20,}")),
    ("anthropic", re.compile(r"sk-ant-[A-Za-z0-9\-_]{20,}")),
    ("openai", re.compile(r"sk-[A-Za-z0-9]{20,}")),
    ("slack", re.compile(r"xox[baprs]-[A-Za-z0-9\-]{10,}")),
    ("google", re.compile(r"AIza[0-9A-Za-z\-_]{35}")),
    ("stripe", re.compile(r"(?:sk|pk|rk)_(?:live|test)_[A-Za-z0-9]{20,}")),
    ("pem", re.compile(r"-----BEGIN[ A-Z]+PRIVATE KEY-----[\s\S]+?-----END[ A-Z]+PRIVATE KEY-----")),
    ("jwt", re.compile(r"eyJ[A-Za-z0-9_\-]{5,}\.eyJ[A-Za-z0-9_\-]{5,}\.[A-Za-z0-9_\-]{5,}")),
    # HTTP Bearer / Basic authorization tokens (value separated by whitespace).
    ("bearer", re.compile(r"(?i)(bearer|basic)\s+[A-Za-z0-9_\-\.\+/=]{16,}")),
    # api-key / token / password / secret assignments (`=` or `:` between name and value).
    ("assign", re.compile(r"(?i)(api[_\-]?key|token|password|secret)\s*[:=]\s*['\"]?[A-Za-z0-9_\-\.\+/=]{16,}['\"]?")),
)


def redact(text: str) -> str:
    """Strip anything that looks like a secret. Idempotent."""
    if not text:
        return text
    out = text
    for label, pat in _SECRET_PATTERNS:
        out = pat.sub(f"[REDACTED:{label}]", out)
    return out


# ---------------------------------------------------------------------------
# Rule-based distillers
# ---------------------------------------------------------------------------

# Conventions we can spot mechanically from tool_usage counts.
_TOOL_CONVENTION_HINTS = {
    "Bash":       "This project drives lots of shell commands — reach for Bash before scripting.",
    "Read":       "Heavy file-reading project — check memory before re-reading known files.",
    "Grep":       "Grep-heavy project — search-first workflow, not blind reads.",
    "WebFetch":   "This project pulls from external docs/URLs frequently.",
    "WebSearch":  "This project depends on live web research.",
    "Edit":       "Predominantly an editing/refactoring codebase — small diffs preferred.",
    "Write":      "New-file-heavy project — likely a scaffolding or generation workflow.",
}


def _dominant_tool_convention(project_path: str) -> tuple[str, str] | None:
    """Return (key, value) if one tool overwhelmingly dominates (>= 40% of calls)."""
    with memory.connect() as conn:
        rows = conn.execute(
            "SELECT tool_name, count FROM tool_usage WHERE project_path = ?",
            (project_path,),
        ).fetchall()
    if not rows:
        return None
    total = sum(r["count"] for r in rows)
    if total < 5:
        return None
    top = max(rows, key=lambda r: r["count"])
    if top["count"] / total < 0.40:
        return None
    hint = _TOOL_CONVENTION_HINTS.get(top["tool_name"])
    if not hint:
        return None
    return (f"tool-dominance:{top['tool_name']}", hint)


def _notes_to_decisions(project_path: str, limit: int = 5) -> list[tuple[str, str]]:
    """Recent user-written notes become explicit `decision` nodes.

    A note the user typed is high-signal — they wanted to record it — so we
    always import the last few as decisions."""
    with memory.connect() as conn:
        rows = conn.execute(
            "SELECT id, note FROM notes WHERE project_path = ? ORDER BY created_at DESC LIMIT ?",
            (project_path, limit),
        ).fetchall()
    out = []
    for r in rows:
        clean = redact(r["note"])
        key = f"note:{r['id']}"       # stable — same note never dupes
        out.append((key, clean))
    return out


def _last_session_gotcha(project_path: str) -> tuple[str, str] | None:
    """If the most recent session had errors, log a gotcha node so next
    session sees 'last time you hit N errors — here's the shape'."""
    with memory.connect() as conn:
        row = conn.execute(
            "SELECT id, errors, tool_calls FROM sessions "
            "WHERE project_path = ? ORDER BY started_at DESC LIMIT 1",
            (project_path,),
        ).fetchone()
    if not row or not row["errors"]:
        return None
    key = f"last-session-errors:{row['id']}"
    val = (f"Previous session had {row['errors']} tool failure(s) out of "
           f"{row['tool_calls']} calls — verify the same class of errors "
           f"before repeating that workflow.")
    return (key, val)


def distill_session(project_path: str) -> list[dict]:
    """Called by the Stop hook. Writes 0..N nodes; returns what was written."""
    written: list[dict] = []

    if hint := _dominant_tool_convention(project_path):
        key, val = hint
        node_id = memory.upsert_node(project_path, "convention", key, redact(val))
        written.append({"id": node_id, "kind": "convention", "key": key})

    for key, val in _notes_to_decisions(project_path):
        node_id = memory.upsert_node(project_path, "decision", key, val)
        written.append({"id": node_id, "kind": "decision", "key": key})

    if gotcha := _last_session_gotcha(project_path):
        key, val = gotcha
        node_id = memory.upsert_node(project_path, "gotcha", key, redact(val))
        written.append({"id": node_id, "kind": "gotcha", "key": key})

    return written


# ---------------------------------------------------------------------------
# Optional Haiku upgrade — only if the `claude` CLI is on PATH.
# ---------------------------------------------------------------------------

def maybe_haiku_upgrade(project_path: str, transcript_snippet: str) -> None:
    """If `claude` is available, ask haiku-4-5 to add a short summary node.
    Fully optional — silently no-ops otherwise."""
    if not shutil.which("claude"):
        return
    if not transcript_snippet or len(transcript_snippet) < 200:
        return
    try:
        prompt = (
            "Below is a snippet of a coding session. In ONE sentence (max 200 chars), "
            "state the single most important decision or fix. If nothing stands out, "
            "reply with exactly: NONE.\n\n"
            f"{transcript_snippet[:6000]}"
        )
        result = subprocess.run(
            ["claude", "-p", "--model", "claude-haiku-4-5", prompt],
            capture_output=True, text=True, timeout=20,
        )
        summary = result.stdout.strip()
        if not summary or summary == "NONE" or len(summary) > 400:
            return
        memory.upsert_node(
            project_path, "decision", f"haiku-summary:{hash(transcript_snippet) & 0xFFFF}",
            redact(summary),
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# .ua/knowledge-graph.json ingester
# ---------------------------------------------------------------------------

def ingest_ua_graph(project_path: str, graph_dict: dict) -> int:
    """Ingest an Understand-Anything KnowledgeGraph payload into our nodes/edges.

    Reads only the fields we care about; silently ignores schema drift.
    Returns the number of nodes actually written."""
    if not isinstance(graph_dict, dict):
        return 0
    nodes = graph_dict.get("nodes") or []
    edges = graph_dict.get("edges") or []

    id_map: dict[str, int] = {}
    written = 0
    for n in nodes:
        try:
            ua_id = n.get("id")
            kind = n.get("type") or "concept"
            name = n.get("name") or ua_id
            summary = n.get("summary") or ""
            fp = n.get("filePath")
            # Store `.ua`-sourced content with a namespaced key so it never
            # collides with our own distiller output.
            key = f"ua:{ua_id}"
            value = redact(f"{name}: {summary}") if summary else redact(name)
            node_id = memory.upsert_node(
                project_path, kind, key, value, file_path=fp,
            )
            id_map[ua_id] = node_id
            written += 1
        except Exception:
            continue

    for e in edges:
        try:
            src = id_map.get(e.get("source"))
            dst = id_map.get(e.get("target"))
            if not src or not dst:
                continue
            kind = e.get("type") or "related"
            w = float(e.get("weight") or 0.5)
            memory.add_edge(project_path, src, dst, kind, weight_delta=w)
        except Exception:
            continue
    return written


def ingest_ua_dir_if_present(project_path: str) -> int:
    """Look for .ua/ or .understand-anything/ inside the project and ingest
    knowledge-graph.json if present. Safe to call every SessionStart."""
    import json
    import os
    for name in (".ua", ".understand-anything"):
        graph_file = os.path.join(project_path, name, "knowledge-graph.json")
        if os.path.exists(graph_file):
            try:
                with open(graph_file) as f:
                    data = json.load(f)
                return ingest_ua_graph(project_path, data)
            except Exception:
                return 0
    return 0
