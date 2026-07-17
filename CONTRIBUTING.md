# Contributing to Claude Pet

Thanks for wanting to help. Claude Pet is deliberately designed so almost every extension is a tiny, self-contained change — most PRs won't need a discussion, just a decent test.

## Ground rules

- **MIT license.** By contributing you agree your changes go under MIT.
- **No new heavy deps.** PySide6, cairosvg, Flask, requests, Pillow are the entire stack. New deps need discussion.
- **100% local, always.** No network calls in the runtime (aside from `claude-pet update` which explicitly hits GitHub, and the `git+…` install path).
- **Tests must stay green on all 3 platforms.** GitHub CI runs the full suite on macOS/Windows/Linux for every push.
- **No secrets in memory ever.** New writes to SQLite must go through `distill.redact()` (or add a new pattern to it) if any field could carry user text.

## Where to add what

| I want to add… | Edit these files | Ballpark size |
|---|---|---|
| **A new emotion** | `src/claude_pet/bot_svg.py` — add one row to `EMOTIONS`, add an eye-render branch in `_render_eye_pair` | ~10 lines |
| **A new ergonomics exercise** | Drop the SVG in `src/claude_pet/ergonomics/svgs/`, append one `Exercise(...)` to `exercises.py`, optionally tweak thresholds in `config.py` | ~6 lines |
| **A new integration** | Anything that can POST to `localhost:5050` (endpoints: `/state`, `/break`, `/version`) can drive the pet. No pet-side code needed. | 0 |
| **A new dashboard tab** | `src/claude_pet/panel.py` — new `QWidget` subclass, add to `MemoryPanel.__init__`'s `tabs.addTab(...)` block | ~50 lines |
| **A theme** | `panel.py` `NEON` dict + `bot_svg.py` `EMOTIONS` colors | palette-only |
| **A CLI subcommand** | `src/claude_pet/cli.py` — add a `cmd_x()` function, an `argparse` sub-parser, an `args.cmd == "x"` branch | ~30 lines |

## Setup

```bash
git clone https://github.com/nikhilagrima/claude-pet.git ~/claude-pet
cd ~/claude-pet
python3 -m venv .venv
source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -e ".[macos]"     # drop [macos] on Windows/Linux
python -m unittest discover -s tests -v
```

## Running the tests

The suite must pass headlessly (Qt runs offscreen via `QT_QPA_PLATFORM=offscreen`).

```bash
python -m unittest discover -s tests
```

If you touch UI code, also open the panel manually:

```bash
claude-pet start
# then click the pet
```

## Style

- **Code**: standard Python, no formatters enforced. Match surrounding style — the codebase prefers explicit over magic.
- **Comments**: only when the WHY isn't obvious. If it's obvious what the code does, don't restate it.
- **Commit messages**: describe what changed AND why, not what future readers should do about it.

## Adding an exercise (worked example)

1. Design your SVG at `viewBox="0 0 220 220"`. If you use CSS custom properties on the root `<svg style="…">`, our `svg_inline.py` resolves them at render time — Qt doesn't natively.
2. Save as `src/claude_pet/ergonomics/svgs/pet-your-exercise.svg`.
3. Add one row to `CATALOG` in `src/claude_pet/ergonomics/exercises.py`:
   ```python
   Exercise(
       slug="your-exercise",
       name="Your Exercise",
       category="shoulders",     # eyes / neck / wrists / posture / hydration / shoulders / ...
       duration_s=25,
       reps=3,
       svg_file="pet-your-exercise.svg",
       instruction="One-line imperative, ≤80 chars.",
   ),
   ```
4. If it's a new category, add it to `ROTATION` in the same file and to `DEFAULT_THRESHOLDS` in `scheduler.py`.
5. Run the tests. Ship the PR.

## Pull request checklist

- [ ] Full test suite green (`python -m unittest discover -s tests`)
- [ ] New public API has a docstring
- [ ] New user-visible behavior mentioned in `README.md`
- [ ] If it's a UI change, screenshots in the PR description
- [ ] No emoji, no new heavy deps, no hardcoded absolute paths

## Where to ask questions

Open a discussion, an issue, or a draft PR — all are fine. There's no gatekeeping around any of them.
