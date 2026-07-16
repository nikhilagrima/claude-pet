<div align="center">

<img src="logo.png" width="200" alt="Claude Pet logo" />

# Claude Pet

**Meet Claude's Virtual Pet Companion for Coders.**

_Made by [Byteflow.bot](https://byteflow.bot) — free & open source._

**v0.3.0 — now a memory brain** for Claude Code. Remembers every project you code in, injects only what matters back into each new session (≤800 tokens, always), learns skills from repeated patterns, and evolves visually as you level up.

</div>

---

## Why?

Claude Code runs in your terminal. If you tab away to Slack, the browser, or another editor, you have **no idea** what Claude is doing — is it thinking? Waiting on you? Failed? Finished?

**Claude Pet fixes that.**

It's a small robot mascot that floats in the bottom-right corner of your screen and reacts to every Claude Code event in real time:

- 🤔 **Purple spinning eyes** — Claude is thinking about your prompt
- 👀 **Cyan scanning eyes** — Claude is reading files
- ✍️ **Blue sparkles** — Claude is writing / editing code
- 🖥️ **Green down-arrows** — Claude is running a bash command
- ✅ **Green ^^ eyes + 3 short dings** — task complete
- ❌ **Red X eyes + low thud** — task failed
- 🔵 **Blue pixel circles + 2 beeps** — Claude needs your attention
- 💤 **Sleeping face + Zzz** — idle for 3 minutes
- ⭐ **Star eyes** — new session started

You always know what's happening — even when you can't see the terminal.

### 🔔 Never miss when Claude needs you

The killer feature: **sound alerts**. When Claude finishes a task, the pet plays **3 short dings**. When Claude is **waiting for your input or permission** — the moment you'd otherwise leave it hanging while you read Slack — the pet plays **2 attention beeps**. Failures get a low thud. You can tab away, grab coffee, work on another screen: your ears tell you the moment Claude needs you back.

### 🧠 v0.3.0 — Memory brain for Claude Code

Claude Pet is no longer just a mascot. It's a **local memory brain**:

- **Graph memory of every project** — decisions, conventions, fixes, gotchas stored in `~/.claude/claude-pet/memory.sqlite` (never bundled, never synced).
- **Automatic context injection** — SessionStart hook emits a ≤800-token ranked block back to Claude Code (`weight × recency × FTS5 match`), so Claude sees what matters on turn 1 without you copy-pasting anything.
- **Ingests `.ua/knowledge-graph.json`** (Understand-Anything format) as authoritative when present.
- **Self-learning skills** — patterns reinforced ≥2× promote themselves to real `SKILL.md` files under `~/.claude/claude-pet/skills/`, with valid frontmatter Claude Code picks up automatically. Tier evolves: 🥚 hatchling → 🐣 apprentice → 🦉 senior → 🦄 ponytail.
- **Click the pet** → a panel opens with Projects, live Graph, Skills, and Stats (including estimated tokens saved).
- **Never-cut safety** — the pet's injection always ends with a ruleset that forbids skipping validation, security, or accessibility to save tokens (adopted from the Ponytail agent framework).

```bash
claude-pet memory                # summary of the current project
claude-pet memory --all          # every project you've ever used
claude-pet note "Working on the auth refactor, next step is JWT rotation"
claude-pet context               # the exact block Claude sees on session start
claude-pet context --budget 400  # smaller injection for tighter turn budgets
```

**Guarantees:** 100% local, no cloud, no embeddings, no vector DB, no new daemon. 11 secret-pattern regexes redact anything sensitive before it touches disk. `.sqlite` files are `.gitignore`d — the package ships fresh empty memory.

<div align="center">

<img src="screenshot.png" width="540" alt="Emotions preview" />

_11 emotions, all rendered live from SVG. Fully transparent window — the mascot floats on your desktop with no chrome, no border, no rectangle._

</div>

---

## Install

### 🚀 Quick install (copy-paste)

**macOS / Linux:**

```bash
git clone https://github.com/nikhilagrima/claude-pet.git ~/claude-pet && cd ~/claude-pet && bash install.sh
```

*(On macOS, use `bash install.command` instead.)*

**Windows (PowerShell):**

```powershell
git clone https://github.com/nikhilagrima/claude-pet.git $HOME\claude-pet
cd $HOME\claude-pet
.\install.bat
```

### 🖱 Double-click install (for non-terminal folks)

1. Download **[the latest release ZIP](https://github.com/nikhilagrima/claude-pet/releases/latest)** and unzip it.
2. Double-click whichever installer matches your OS:

   | OS | File |
   |---|---|
   | macOS | `install.command` *(right-click → Open the first time)* |
   | Windows | `install.bat` |
   | Linux | `install.sh` |

3. Wait ~60 seconds. Done — the mascot appears in your bottom-right corner.

### 📦 Pre-built app (macOS)

Download **[Claude Pet.app](https://github.com/nikhilagrima/claude-pet/releases/latest)** from Releases, drag to `/Applications`, double-click. No Python required.

---

## What the installer does

- Checks Python 3.10+
- Installs Homebrew (macOS, if missing)
- Installs Cairo graphics library (all platforms)
- Creates a Python venv
- Installs the `claude-pet` package
- Writes Claude Code hooks to `~/.claude/settings.json` (real-time reactions)
- Starts the mascot in the background

The installer is idempotent — safe to run multiple times.

---

## How to use it

- **Click** the mascot → curious reaction
- **Drag** it → move it anywhere
- **Right-click** → context menu (Hello / Working / Celebrate / Sleep / Reset / Quit)
- **After 3 min idle** → falls asleep with animated Zzz's
- **Every 30s during idle** → briefly shows the current time

The mascot **stays on top of every application** (including fullscreen apps and other Spaces on macOS), so you never lose sight of it.

---

## CLI reference

```
claude-pet run                Run pet + server in this terminal (foreground)
claude-pet start              Start pet + server detached in the background
claude-pet stop               Kill running pet
claude-pet install-hooks      Add Claude Code hook entries to ~/.claude/settings.json
claude-pet uninstall-hooks    Remove them again
claude-pet hook <event>       Internal — invoked by Claude Code hooks
claude-pet memory             Show saved history for the current project
claude-pet memory --all       List every remembered project
claude-pet note <text...>     Attach a note to the current project
claude-pet context            Print the context block Claude sees on session start

Options:
  --show-in-dock              macOS: show the pet icon in the Dock / Cmd-Tab
                              (default: accessory-app mode, no Dock icon)
```

---

## Sound cues

| Event | Sound | Pattern |
|---|---|---|
| Task complete (Stop) | Glass ding | **3× beeps** |
| Needs attention (Notification / UserPromptSubmit) | Morse ping | **2× beeps** |
| Task failed (PostToolUseFailure) | Basso thud | 1× |

macOS uses built-in system sounds. Windows falls back to `Windows Notify System Generic.wav`. Linux uses `freedesktop`'s `complete.oga` / `dialog-error.oga`. If none are found, bundled fallback WAVs play.

---

## Architecture

```
claude-pet/
├── src/claude_pet/
│   ├── app.py         PySide6 frameless transparent window (all platforms)
│   ├── bot_svg.py     Live SVG generator — 11 emotions × animation frames
│   ├── server.py      Local Flask on :5050 holding current state
│   ├── hook.py        Called from Claude Code hooks → maps event to emotion
│   ├── cli.py         `claude-pet` subcommands
│   └── assets/        Icons + fallback sounds
├── install.command / install.bat / install.sh   Double-clickable installers
├── build.command / build.bat / build.sh         PyInstaller build scripts
└── claude_pet.spec                              PyInstaller config
```

Data flow: **Claude Code event → hook.py → HTTP POST to :5050 → pet polls and renders new emotion.**

---

## Build your own binary

```bash
# macOS
./build.command   # produces dist/Claude Pet.app

# Windows
build.bat         # produces dist\claude-pet.exe

# Linux
./build.sh        # produces dist/claude-pet
```

Uses PyInstaller. Cross-compilation isn't supported — you must build on the target OS.

---

## Uninstall

```bash
claude-pet stop
claude-pet uninstall-hooks
pip uninstall claude-pet
```

That's it — no daemon left running, no LaunchAgent, no scheduled task.

---

## Requirements

- **Python 3.10 or newer**
- **macOS 11+** (Big Sur or later)
- **Windows 10+**
- **Linux with X11 or Wayland compositor** (GNOME, KDE, Sway all tested)

---

## Contributing

PRs welcome — bugs, new emotions, alternative mascot designs (the SVG generator in `bot_svg.py` is easy to extend).

## License

MIT © 2026 [Byteflow.bot](https://byteflow.bot). Do whatever you want.

---

<div align="center">

_Built with love for the Claude Code community by [**Byteflow.bot**](https://byteflow.bot)._

</div>
