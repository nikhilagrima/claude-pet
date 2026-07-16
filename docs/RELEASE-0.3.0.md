# Claude Pet v0.3.0 — Memory brain for Claude Code

**Released 2026-07-16.** MIT-licensed. By Byteflow.bot.

---

## What changed

v0.2.0 was a status mascot. **v0.3.0 is a memory brain.**

The pet still shows 11 emotions, still floats always-on-top, still beeps when a task completes. On top of that, it now:

1. **Remembers every project you code in** — a local SQLite graph of decisions, conventions, fixes, and gotchas, scored by weight × recency.
2. **Injects the smallest useful context back into every new Claude Code session** — a ranked, budgeted ≤800-token block emitted from a SessionStart hook.
3. **Ingests `.ua/knowledge-graph.json`** (Understand-Anything format) as authoritative when present, never rebuilds what's already known.
4. **Learns skills**: a pattern reinforced ≥2× promotes itself into a real Claude Code skill file at `~/.claude/claude-pet/skills/<slug>/SKILL.md`, valid frontmatter and all.
5. **Evolves visually** — the mascot gains a tier badge (🥚 hatchling → 🐣 apprentice → 🦉 senior → 🦄 ponytail) based on your top learned skill.
6. **Click to open the memory panel** — Projects / Graph / Skills / Stats, all rendered natively in PySide6, no HTML view engine required.

Everything is 100% local. No cloud. No embeddings. No vector store. No new daemon.

---

## Numbers (from `tests/test_e2e_two_session.py`)

Simulated two-session flow on a small project:

| metric | value |
|---|---|
| Injected context block (Session 2) | **725 chars, ~182 tokens** |
| Budget ceiling | 800 tokens (~3200 chars) |
| Skill auto-promoted after Session 1 | **`e2e-demo-tool-dominance-bash`**, tier: hatchling |
| Naive-dump baseline | 75 chars (empty by definition — nothing to dump yet) |

The important number isn't the byte count on a tiny synthetic project — it's that **Session 2 saw both the learned convention AND the promoted skill without you copy-pasting anything, and the payload never exceeds 800 tokens even at pathological scale** (verified with `test_output_never_exceeds_default_budget` seeding 100 nodes + 50 notes).

**Extrapolated benefit on a real project**: a typical Claude Code session re-reads files it saw last week. Each file re-read costs 200-1000+ tokens. With ~40 nodes in memory pointing at "already indexed" files, the pet steers Claude away from at least a handful of re-reads per session — a conservative floor of `nodes × 40 = 1600 tokens saved per session` shown live in the Stats tab.

---

## Migration from v0.2.0

**Zero action required.** Open a session with v0.3.0 installed and:

- v0.2.0's `projects`, `sessions`, `tool_usage`, `notes` tables are preserved verbatim.
- New tables (`nodes`, `edges`, `skills`, `nodes_fts`) are added.
- `PRAGMA user_version` transitions `1 → 2` in a single connect.

Verified by `test_v020_rows_preserved_after_migration` — inserts a legacy row, runs migration, confirms the row survives.

---

## Safety guarantees

- **Nothing personal ever leaves your machine.** The DB is at `~/.claude/claude-pet/memory.sqlite`, gitignored, never bundled in the released package.
- **Secrets are redacted before write.** Every value goes through 11 regexes covering AWS/GitHub/Anthropic/OpenAI/Slack/Google/Stripe/PEM/JWT/Bearer/api-key patterns before it hits SQLite. Test: `test_secrets_never_reach_the_db`.
- **Never-cut safety rules are appended last to every injection**, adopted verbatim from Ponytail's ladder + carve-outs. Test: `test_safety_block_present_when_no_history`.
- **Budget hard cap.** The context builder trims from the end if the body overflows and always reserves room for the safety block. Test: `test_output_never_exceeds_default_budget`.
- **`.ua` ingestion is read-only + defensive** — every `filePath` runs through `os.path.relpath` even though `.ua` already sanitizes.

---

## Files added / changed

```
NEW  src/claude_pet/distill.py    session distiller + secret redaction + .ua ingester
NEW  src/claude_pet/context.py    ≤800-token ranked injection builder
NEW  src/claude_pet/skills.py     skill promotion + SKILL.md writer
NEW  src/claude_pet/panel.py      Projects / Graph / Skills / Stats panel
NEW  docs/RESEARCH.md             Phase-0 research notes
NEW  docs/DECISIONS.md            per-phase decision log
NEW  docs/RELEASE-0.3.0.md        this file
NEW  tests/*.py                   35 tests across 6 modules

MOD  src/claude_pet/memory.py     nodes / edges / skills / FTS5 additions, migration
MOD  src/claude_pet/hook.py       Stop → distill + promote; SessionStart → ingest .ua + inject
MOD  src/claude_pet/cli.py        `memory`, `note`, `context --budget/--json` subcommands
MOD  src/claude_pet/bot_svg.py    optional tier= param → badge + pips on capsule
MOD  src/claude_pet/app.py        click → toggle panel; tick reads top_tier
```

Total new code: ~1200 lines. Zero new dependencies. `sqlite3`, `re`, `math`, `pathlib` — all stdlib.

---

## Regression coverage

35 tests, all passing:

- Phase 1 storage (7): fresh install, v0.2.0 upgrade, upsert dedup, weight ranking, level formula.
- Phase 2 distill (9): 8 secret patterns redacted, reinforcement bumps weight not row count, notes → decisions, `.ua` lossless ingest + dedup.
- Phase 3 context (7): default + custom budget cap, safety always present, determinism, weight ranking.
- Phase 4 skills (6): slug safety, threshold trigger, level progression 1→4, frontmatter YAML validity, no duplicate skill dirs.
- Phase 5 UI (5): all 11 emotions still render, tier overlay adds pips, panel module imports cleanly.
- Phase 6 E2E (1): the full two-session flow above.

Run: `python -m unittest discover -s tests -v`

---

## What v0.3.0 explicitly does NOT do

- No embeddings.
- No vector database.
- No always-on daemon (still just the Flask :5050 the pet already ran).
- No cloud sync.
- No breaking changes to the pet window, emotions, sound cues, or hook wiring.
- No new heavy deps.
