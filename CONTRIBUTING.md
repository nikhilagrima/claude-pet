# Contributing to Claude Pet

Thanks for being here. Claude Pet is a desktop companion for Claude Code, and it's built to be forked, extended and argued with. Small contributions are genuinely welcome — most of the pet's personality came from tiny PRs.

## Quick start

```bash
git clone https://github.com/<your-username>/claude-pet.git
cd claude-pet
python3 -m venv .venv && source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -e ".[macos]"                              # drop [macos] on Windows/Linux
claude-pet run                                         # foreground pet + local server on :5050
python -m unittest discover -s tests                   # 128 tests, headless via QT_QPA_PLATFORM=offscreen
```

If any of the above fails on a clean machine, that's a bug — please open an issue. Setup friction is the thing we most want to hear about.

## How the project is laid out

| Area | What lives there | Good for |
|---|---|---|
| Hook layer | `src/claude_pet/hook.py` — listens to Claude Code hook events and maps them to pet reactions | Event pipeline work, new event types |
| Emotions | `src/claude_pet/bot_svg.py` — each emotion is a single row in `EMOTIONS` mapping a state to an eye + colour | First-time contributors |
| Memory brain | `src/claude_pet/memory.py`, `distill.py`, `context.py` — per-project SQLite graph of decisions, conventions and fixes; ranks and injects a compact recap on SessionStart | Retrieval, ranking, token budgeting |
| Skills | `src/claude_pet/skills.py` — promotes repeated patterns into `SKILL.md` files at `~/.claude/skills/` | Pattern detection |
| Ergonomics | `src/claude_pet/ergonomics/` — timed break prompts; pet demonstrates each exercise via SVG animation | Animation, health research |
| GitHub watcher | `src/claude_pet/github_watch/` — polls repo activity (commits, PRs, reviews, CI, deploys) and surfaces it as pet reactions + toasts | API work, notifications |
| Dashboard | `src/claude_pet/panel.py` — HUD panel with Projects, Graph, Skills, Stats, Ergonomics, GitHub tabs | Qt/PySide6 UI work |

## Ways to help

**Start here if you're new:** issues labeled [`good first issue`](https://github.com/nikhilagrima/claude-pet/labels/good%20first%20issue). Adding an emotion or an exercise is usually a handful of lines.

Also always useful:
- Bug reports with your OS + Claude Code version
- A blunt review of the memory graph design (open a Discussion — we'd rather argue now than refactor later)
- Docs fixes, typos, unclear README sections
- Interop with other Claude Code tools (statuslines, HUDs, memory tools) — we're happy to build toward other projects and link them

## Design rules (please read before an animation PR)

These are non-obvious and PRs get sent back without them:

1. **Monochrome only.** Backgrounds `#000000`/`#0A0A0A`, primary `#FFFFFF`, greys `#A0A0A0`/`#B8B8B8`, borders `#1F1F1F`. The only colour permitted anywhere is in the emotion eye glyphs.
2. **Transparent backgrounds.** Desktop pet SVGs must not bake in a background rect.
3. **The mascot is fixed.** Dark glossy capsule visor, metallic ring border, grey gloss swoosh top-right, small glint bottom-left. No antenna, no white body.
4. **Exercises animate both hands/arms** — never one side only — with continuous visible motion.
5. **Local only.** No network calls, no telemetry, no accounts. Ever. If a feature seems to need a server, open an issue first and we'll find another way.

## Pull requests

- Branch from `main`, one logical change per PR
- Run the test suite before pushing; add tests for new behaviour (`python -m unittest discover -s tests`)
- Keep the description short: what changed, why, how you tested it
- Cross-platform matters — note which OS you tested on. We support macOS, Windows and Linux, and CI runs all three
- Draft PRs are welcome if you want early feedback

We aim to respond within 24 hours. If we haven't, ping the thread — it's an oversight, not disinterest.

## Reporting bugs

Include: OS + version, Claude Code version, what you expected, what happened, and any relevant output (`claude-pet doctor` output is especially useful — it dumps hook wiring + macOS always-on-top state). A GIF of the pet misbehaving is worth a thousand words and is genuinely fun to receive.

## Security

Please don't open public issues for security problems. Email nikhil@differentbyte.in instead.

## Code of conduct

Be decent. Assume good faith, critique code rather than people, and remember that most contributors here are doing this after a full day of other work. Harassment of any kind means you're out.

## Licence

By contributing you agree your work is released under the [MIT Licence](./LICENSE), same as the rest of the project.
