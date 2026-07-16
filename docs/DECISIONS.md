# Claude Pet v0.3.0 — decision log

Chronological. Each phase appends: **cut**, **reused**, **budget proof**, **migration safety**, **secret audit**.

---

## Phase 0 — research

**Cut**: none (research-only phase).
**Reused**: v0.2.0's `sqlite3` stdlib import and `~/.claude/claude-pet/memory.sqlite` path — Phase 1 extends the same file, no new DB path.
**Budget proof**: n/a for research. Injection budget math in `RESEARCH.md §6` proves 800 tokens (~3200 chars) fits every section with the safety block appended last.
**Migration safety**: v0.2.0 tables frozen — Phase 1 only ADDs new tables. `PRAGMA user_version` gates all migrations.
**Secret audit**: Understand-Anything's ingester runs every `filePath` through `os.path.relpath` defensively before write.

---

## Phase 1 — Storage v2

**Cut**: no need for a separate `graph.py` — additive schema lives in `memory.py` alongside the v0.2.0 tables. Fewer imports, one place to reason about migrations.
**Reused**: existing `_upsert_project` helper, `_now()` timestamp helper, and `connect()` context manager — extended, not duplicated. `sqlite3.Row` factory carried through.
**Budget proof**: n/a (storage layer).
**Migration safety**: `test_v020_rows_preserved_after_migration` inserts a v0.2.0-style row, runs migration, verifies the row survives. All CREATE statements use `IF NOT EXISTS`.
**Secret audit**: schema-only phase, no user data touched. FTS5 tokenizer set to `porter unicode61` — no external tokenizer that could leak content.

---

## Phase 2 — Distiller + `.ua` ingester

**Cut**: no LLM call in the default path. Rule-based distiller (`_dominant_tool_convention`, `_notes_to_decisions`, `_last_session_gotcha`) covers the common cases without a network hop. `maybe_haiku_upgrade` is opt-in, silently no-ops without `claude` on PATH.
**Reused**: `memory.upsert_node` and `memory.add_edge` — the write API is symmetric between our distiller and the `.ua` ingester. `_upsert_project` still handles first-seen bookkeeping.
**Budget proof**: distiller writes at most `1 + N_notes(≤5) + 1` = 7 nodes per Stop. Deduped by (project, kind, key) — repeated Stops bump `weight` not row count. `test_same_fact_twice_bumps_weight_not_row_count`.
**Migration safety**: only new tables touched.
**Secret audit**: 11 regexes in `_SECRET_PATTERNS`, applied in `redact()` before every DB write. 9 shapes tested in `test_redact_scrubs_common_secret_shapes`. Idempotent (`test_redact_is_idempotent`). `test_secrets_never_reach_the_db` proves end-to-end.

---

## Phase 3 — Context engine

**Cut**: no tiktoken dep. Chars/token proxy (4:1) is deterministic and free. If we later want exact counts, we can wire a real tokenizer behind a flag — the API doesn't change.
**Reused**: `memory.project_summary`, `memory.list_skills`, `memory.top_nodes`. New `top_nodes` ranking function is the only new query.
**Budget proof**: `test_output_never_exceeds_default_budget` seeds 100 nodes + 50 notes + 30 skills and asserts output ≤ 3200 chars + 50-char joining slack. Custom budgets tested similarly. Safety block is length-capped at 100 chars and always appended.
**Migration safety**: read-only phase.
**Secret audit**: injection reads from `nodes.value`, which was already redacted on write.

---

## Phase 4 — Skill promotion

**Cut**: no complex NLP for skill titles — `_slugify` + `_title_case` from stdlib `re` is enough. Descriptions come straight from `node.value` (already redacted).
**Reused**: `memory.upsert_skill` (extended with authoritative `reinforcements=` override so the skill mirrors its source node).
**Budget proof**: `_skill_body` truncates description to 400 chars — well under Claude Code's per-skill listing budget (1536 chars default).
**Migration safety**: `skills` table is new (v2), no impact on v0.2.0.
**Secret audit**: `SKILL.md` body writes `node.value` verbatim — already redacted. `_slugify` restricts filenames to `[a-z0-9-]+`, no traversal risk.

---

## Phase 5 — UI

**Cut**: no `QWebEngineView` (adds ~200MB to the built binary). Force-layout graph rendered natively via `QGraphicsScene`. Deterministic circular layout — no spring simulation to converge.
**Reused**: existing `QApplication` / `QDialog` classes; the pet's click handler is the only new binding.
**Budget proof**: n/a (UI layer, not injected).
**Migration safety**: panel is opt-in — never opened unless user clicks. `_toggle_panel` catches import failures silently.
**Secret audit**: displays same redacted values from the DB; no new data source.

---

## Phase 6 — Benchmark + release

**Cut**: no comparison against v0.2.0 numbers — v0.2.0 had no injection, so "improvement" is trivially infinite. Real comparison is against a *naive dump* baseline (`test_full_two_session_flow` prints both).
**Reused**: every phase's tests run as part of `unittest discover` — 35 total.
**Budget proof**: E2E test asserts injection ≤ 800 tokens with real 2-session data.
**Migration safety**: v0.2.0 test still green after all v0.3.0 additions.
**Secret audit**: full test suite includes `test_secrets_never_reach_the_db`; passes.
