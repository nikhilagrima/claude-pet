# Claude Pet v0.3.0 — Research findings

Written 2026-07-16 during Phase 0. Everything here is grounded in code I actually read (see citations), not marketing copy.

---

## 1. v0.2.0 codebase inventory

Files touched by this upgrade:

| file | lines | role |
|---|---|---|
| `src/claude_pet/memory.py` | 304 | SQLite: `projects`, `sessions`, `tool_usage`, `notes` |
| `src/claude_pet/hook.py` | 125 | Event → emotion mapping, `_remember()` writes, SessionStart JSON injection stub |
| `src/claude_pet/cli.py` | 298 | subcommands: run/start/stop/install-hooks/hook/memory/note/context |
| `src/claude_pet/app.py` | 418 | PySide6 pet window (11 emotions, always-on-top) |
| `src/claude_pet/bot_svg.py` | 294 | Live SVG generator |
| `src/claude_pet/server.py` | 28 | Flask state on :5050 |
| `src/claude_pet/__main__.py` | 4 | `python -m claude_pet` entry |

v0.2.0's schema is stable and populated on my dev machine. **Migration must preserve it** — no drops, no renames, additive only.

---

## 2. Ponytail — reuse-before-rebuild ladder (adopted)

Source: https://github.com/DietrichGebert/ponytail (`AGENTS.md`).

The ladder is a **prescriptive ordered check** (not a weighted score) that the injected ruleset applies *after* the agent understands the problem:

```
1. Does this need to exist?        → skip (YAGNI)
2. Already in this codebase?       → reuse it
3. Stdlib does it?                 → use it
4. Native platform feature?        → use it
5. Installed dependency?           → use it
6. One line?                       → one line
7. Only then: the minimum that works
```

**Adopted verbatim** into the ≤80-token safety ruleset injected on SessionStart (Phase 3). Key insight: ponytail is *prompt-injection based*, no DB. Its power comes from **compression + always-on presence** in every session, not retrieval.

### Ponytail's "never cut safety" carve-out (adopted)

> "trust-boundary validation, data-loss handling, security, and accessibility are never on the chopping block"

Encoded as a hard line in the injected ruleset. The pet **never** invents shortcuts around: input validation, error handling that prevents data loss, security checks, accessibility.

### Ponytail's honest benchmarking (adopted for Phase 6)

Ponytail's Tier-1 real-agent benchmark:
- 12 tickets on `tiangolo/full-stack-fastapi-template`
- n=4 runs per ticket, Haiku 4.5
- Metrics: LOC (`git diff`), tokens, cost, time, safety (binary)
- Baselines: no-skill, "caveman" terse prose, "yagni+one-liners" prompt

**Our Phase-6 benchmark will use the same structure** but scaled to a 2-session simulation (session 1 = learning; session 2 = with-injection). Real token counts, no marketing math.

---

## 3. Understand-Anything — `.ua/knowledge-graph.json` schema (ingester target)

Source: `understand-anything-plugin/packages/core/src/types.ts` and `persistence/index.ts`.

### Directory layout on disk (per project root)

```
.ua/                          ← current name (or .understand-anything/ if legacy)
├── knowledge-graph.json      ← THE graph (only file we ingest)
├── meta.json                 ← AnalysisMeta (skip)
├── fingerprints.json         ← file hashes (skip)
└── config.json               ← autoUpdate + language (skip)
```

Legacy dir `.understand-anything/` also supported (persistence.ts `resolveUaDirName`).

### `KnowledgeGraph` root shape

```typescript
{
  version: string,
  kind?: "codebase" | "knowledge" | "design",
  project: { name, languages[], frameworks[], description, analyzedAt, gitCommitHash },
  nodes: GraphNode[],
  edges: GraphEdge[],
  layers: Layer[],           // logical groupings (nodeIds[])
  tour: TourStep[]           // learn-mode walkthrough
}
```

### `GraphNode`

```typescript
{
  id: string,
  type: NodeType,              // 27 values, see below
  name: string,
  filePath?: string,           // ALWAYS relative in a well-formed graph (sanitised on save)
  lineRange?: [number, number],
  summary: string,
  tags: string[],
  complexity: "simple" | "moderate" | "complex",
  languageNotes?: string,
  domainMeta?, knowledgeMeta?, figmaMeta?   // optional per-kind metadata
}
```

**27 NodeType values** (I map these onto our `nodes.kind` column as-is):

- **Code (5):** `file`, `function`, `class`, `module`, `concept`
- **Non-code (8):** `config`, `document`, `service`, `table`, `endpoint`, `pipeline`, `schema`, `resource`
- **Domain (3):** `domain`, `flow`, `step`
- **Knowledge (5):** `article`, `entity`, `topic`, `claim`, `source`
- **Design (6):** `page`, `screen`, `component`, `componentSet`, `instance`, `token`

### `GraphEdge`

```typescript
{
  source: string,   // node id
  target: string,
  type: EdgeType,   // 38 values across 9 categories
  direction: "forward" | "backward" | "bidirectional",
  description?: string,
  weight: number    // 0-1
}
```

**Adopted EdgeType categories** (I mirror onto our `edges.kind`): Structural (`imports`/`exports`/`contains`/`inherits`/`implements`), Behavioral (`calls`/`subscribes`/`publishes`/`middleware`), Data flow (`reads_from`/`writes_to`/`transforms`/`validates`), Dependencies (`depends_on`/`tested_by`/`configures`), Semantic (`related`/`similar_to`), Infrastructure (`deploys`/`serves`/`provisions`/`triggers`), Schema/Data (`migrates`/`documents`/`routes`/`defines_schema`), Domain (`contains_flow`/`flow_step`/`cross_domain`), Knowledge (`cites`/`contradicts`/`builds_on`/`exemplifies`/`categorized_under`/`authored_by`), Design (`instance_of`/`variant_of`/`uses_token`).

### Ingestion rule (Phase 2)

`.ua/knowledge-graph.json` **is authoritative** when present — we ingest it into our `nodes`/`edges` tables (project-scoped) and **never rebuild** what it already knows. Our own distiller only writes the categories `.ua` doesn't cover: `decision`, `convention`, `fix`, `gotcha` (all mapped to `nodes.kind` = one of those literals; we don't reuse `.ua`'s NodeType enum for these).

**Path safety**: persistence.ts sanitises absolute paths to relative before writing. We trust `filePath` is already project-relative on ingest — but our ingester still runs it through `os.path.relpath` defensively.

---

## 4. Claude Code hooks — payloads we rely on

From the settings.json schema I inspected earlier this session:

### SessionStart

- No inputs on stdin beyond `session_id`, `cwd`, `project_dir`.
- **Stdout can be a JSON envelope** that gets injected into the model:
  ```json
  {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "…"}}
  ```
- Non-JSON stdout is IGNORED (I verified this in v0.2.0 — printing plain text to stdout from a hook does NOT inject).
- **This is our injection channel.** Everything Phase 3 assembles is written here as a single string, ≤800-token by construction.

### PreToolUse

- stdin: `{ session_id, tool_name, tool_input, cwd, project_dir }`
- We use `tool_name` for emotion classification and for `tool_usage` counter increments.
- Not our injection point (Ponytail injects here for subagents; we only observe).

### Stop

- Fires when the model's turn ends normally. stdin: `{ session_id, cwd, project_dir }`.
- **This is our distiller trigger.** Phase 2 uses Stop to summarise the session and upsert nodes/edges.
- Stop can OPTIONALLY read the session transcript via `~/.claude/projects/<slug>/*.jsonl` — but that's fragile. Rule-based distiller works from `tool_usage` deltas + last N `notes` for the project. Optional Haiku call gets a text snapshot.

### PostToolUseFailure

- Fires when a tool call errored. Emits `error` state; we also record it as a `fix` node candidate (Phase 4) if the *next* successful edit follows within the same session.

---

## 5. Storage v2 schema (Phase 1 target)

Migrated in place from v0.2.0. Additive only — v0.2.0 tables and rows untouched.

```sql
-- Existing (v0.2.0), unchanged:
--   projects, sessions, tool_usage, notes

CREATE TABLE IF NOT EXISTS nodes (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  project_path  TEXT NOT NULL,
  kind          TEXT NOT NULL,   -- .ua NodeType OR "decision"/"convention"/"fix"/"gotcha"
  key           TEXT NOT NULL,   -- stable identifier for dedup within (project, kind)
  value         TEXT NOT NULL,   -- the summary/text (indexed by FTS)
  weight        REAL NOT NULL DEFAULT 1.0,
  reinforcements INTEGER NOT NULL DEFAULT 1,
  file_path     TEXT,            -- optional, relative to project root
  created_at    TEXT NOT NULL,
  last_seen     TEXT NOT NULL,
  UNIQUE(project_path, kind, key)
);

CREATE INDEX IF NOT EXISTS idx_nodes_project ON nodes(project_path);
CREATE INDEX IF NOT EXISTS idx_nodes_weight  ON nodes(project_path, weight DESC);
CREATE INDEX IF NOT EXISTS idx_nodes_last    ON nodes(project_path, last_seen DESC);

CREATE TABLE IF NOT EXISTS edges (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  project_path TEXT NOT NULL,
  src_id       INTEGER NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
  dst_id       INTEGER NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
  kind         TEXT NOT NULL,   -- .ua EdgeType or our own literals
  weight       REAL NOT NULL DEFAULT 1.0,
  UNIQUE(project_path, src_id, dst_id, kind)
);

CREATE TABLE IF NOT EXISTS skills (
  slug          TEXT PRIMARY KEY,
  title         TEXT NOT NULL,
  description   TEXT NOT NULL,
  level         INTEGER NOT NULL DEFAULT 1,        -- floor(log2(reinforcements))+1
  tier          TEXT NOT NULL,                     -- hatchling|apprentice|senior|ponytail
  reinforcements INTEGER NOT NULL DEFAULT 1,
  project_paths TEXT NOT NULL,                     -- JSON array
  source_node_ids TEXT NOT NULL,                   -- JSON array of node ids
  created_at    TEXT NOT NULL,
  last_used     TEXT NOT NULL,
  disk_path     TEXT                               -- ~/.claude/claude-pet/skills/<slug>/SKILL.md
);

CREATE VIRTUAL TABLE IF NOT EXISTS nodes_fts USING fts5(
  value, tokenize='porter unicode61'
);
-- Populated + kept in sync via AFTER INSERT/UPDATE/DELETE triggers.

PRAGMA user_version = 2;
```

**Migration algorithm** (idempotent):

1. Read `PRAGMA user_version`. If already ≥ 2, no-op.
2. If 0 (fresh install), apply everything above.
3. If 1 (v0.2.0), create new tables + FTS; do NOT touch existing tables; set `user_version = 2`.

**Why FTS5, not embeddings**: hard constraint says no vector DB. FTS5 is stdlib-adjacent (ships with the SQLite Apple/Ubuntu Python builds; we detect and fall back to `LIKE` if missing).

---

## 6. Context injection budget (Phase 3)

Default **800 tokens ≈ 3200 chars** (4:1 chars-per-token rough proxy; deterministic, no tiktoken dep).

Composition (approximate maximums, enforced by trim-in-order):

| section | max chars | content |
|---|---|---|
| Header + project summary | 300 | "Claude Pet remembers this project: N sessions, first seen …, top tools X/Y/Z" |
| Last session recap | 400 | Ended-at + tool call count + errors + last 2 notes |
| Top-N ranked nodes | 1800 | ordered by `weight * recency * fts5_match` |
| Skills manifest | 400 | active skills with tier icons |
| Already-known files | 200 | short list "already indexed: …" so Claude skips re-reading |
| Safety ruleset | 100 | ponytail-derived; NEVER trimmed |

**Ranking formula**: `score = weight * recency_decay * (1 + fts_bm25_boost)`

- `recency_decay = exp(-hours_since_last_seen / 168)` (168h half-life ≈ 1 week)
- `fts_bm25_boost = 0` if no query context, else BM25 score from FTS5
- Ties broken by `last_seen DESC` for determinism

The safety ruleset block is **appended last** and is not counted against user-facing content — always fits.

---

## 7. Skill promotion rules (Phase 4)

Trigger: a node's `reinforcements >= 2` → promote to a skill on disk.

- `slug = kebab_case(project_slug + "-" + node.key[:40])`
- `level = floor(log2(reinforcements)) + 1` → 2×=level 1, 4×=level 2, 8×=level 3, 16×=level 4
- `tier`: `hatchling`(1), `apprentice`(2), `senior`(3), `ponytail`(4+)
- Disk: `~/.claude/claude-pet/skills/<slug>/SKILL.md` — never in the git repo.

**Skill frontmatter**:
```yaml
---
name: <Title Case slug>
description: <the node.value truncated to 300 chars>
metadata:
  tier: <hatchling|apprentice|senior|ponytail>
  level: <int>
  reinforcements: <int>
  source_project: <first project path this pattern was seen in>
---
```

---

## 8. Non-goals for v0.3.0

- No embeddings, no vector store, no ANN index.
- No new daemon or background process.
- No cloud upload of anything — 100% local.
- No breaking changes to the pet window, emotions, sound cues, or hook wiring.
- No new heavy deps (Qt, cairosvg, Flask stay the total set).

---

## 9. Decision log lives at `docs/DECISIONS.md`

Every phase appends one paragraph noting: what was cut, what was reused, budget-fit proof, migration-safety proof, secret-leak audit outcome.
