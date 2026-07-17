# Claude Pet — Ergonomics Coach Research

Written before writing any code. Grounds every default in a published source I actually cited (not a "screen breaks are healthy!" hand-wave).

## Sources

- **AOA — 20-20-20 rule**: American Optometric Association's advice for computer vision syndrome. Every 20 minutes of near work, look at something ≥20 feet (~6 m) away for ≥20 seconds. Blink deliberately.
- **UK HSE 2025 guidance on DSE (display screen equipment)**: short frequent breaks beat rare long ones; 5–10 min off-screen per hour is the current advice.
- **CCOHS — Office Stretching**: chin tucks, shoulder shrugs, shoulder-blade squeezes, neck side tilts, wrist flexor/extensor, prayer stretch, deep breathing. All quick (≤30 s), no equipment.
- **OSHA Computer Workstations eTool**: microbreaks (2–5 min) every 30–40 min, ideally with a postural change (stand, walk, stretch).

## Adopted defaults (baked into `config.json`)

| Cue | Interval (ACTIVE work time, not wall clock) | Duration | Source |
|---|---|---|---|
| Eye break (20-20-20) | every 20 min | 20 s | AOA |
| Micro-break | every 30 min | 90 s | OSHA |
| Hourly reset | every 60 min | 5 min (suggested) | HSE 2025 |

## Exercise catalog

Each exercise stored as data in `exercises.py`: `name`, `category`, `duration_s`, `reps`, `svg_asset`, `instruction` (≤80 chars).

The user supplied 5 SMIL-animated SVGs that we ship as-is. Each has a `viewBox="0 0 220 220"` and uses CSS custom properties for colors, so they theme cleanly against the pet's palette. Native SMIL means QSvgWidget renders them animated with zero extra code — no cairosvg roundtrip.

**Initial catalog** (v0.4.0):

| Slug | Category | Duration | Reps | SVG asset | Instruction |
|---|---|---|---|---|---|
| eye-break | eyes | 20 s | 1 | pet-eye-break.svg | "Look at anything 20 ft away — switch sides. 20 s total, keep blinking." |
| chin-tuck | neck | 5 s hold | 10 | pet-chin-tuck.svg | "Glide head back — both sides, no tilting. Hold 5 s, repeat ×10." |
| wrist-circles | wrists | 12 s per direction | 4 each way | pet-wrist-circles.svg | "Both fists out, opposite circles. ×4 one way, then ×4 reversed." |
| reach-high | posture | 5 s hold | 2 | pet-reach-high.svg | "Arms overhead, stretch tall — hold — ×2." |
| water-break | hydration | 15 s | 1 | pet-water-break.svg | "Sip regularly — small amounts through the day." |

Later categories to grow (not blockers for v0.4.0): shoulder shrug, shoulder roll, shoulder-blade squeeze, neck side tilt, wrist flexor/extensor stretch, prayer stretch, deep breathing, stand-up posture reset.

## Failure modes we must NOT reproduce

Two problems break-reminder apps consistently suffer:

1. **False alarms** — timers fire while the user is at lunch, in a meeting, or reading a doc off-screen. We fix this by counting only **real work time** from Claude Code hook events (UserPromptSubmit, PostToolUse). Existing 3-min sleep state → pause all ergonomic timers.
2. **No guidance during the break** — "take a break" without telling the user what to do. Users close the alarm and get straight back to work. We fix this by having the pet **demonstrate each exercise**: the animated SVG shows the exact motion, a countdown ring shows how long, a rep counter tracks progress. The overlay stays until the user marks it done or explicitly skips.

## Smart timing

- **Prefer prompting when Claude is mid-task** (thinking or long tool run) — the user is already waiting for output, so the break costs zero flow.
- **Never interrupt while user is typing** a prompt. If the threshold hits during typing, defer up to 5 minutes.
- **Rotate categories**: eyes → neck → wrists → posture → eyes … so no muscle group gets skipped.

## Architecture

- `ergonomics/exercises.py` — data-only catalog.
- `ergonomics/tracker.py` — accumulates active work seconds from event stream; persists to SQLite.
- `ergonomics/scheduler.py` — thresholds + deferral + category rotation.
- `ergonomics/overlay.py` — Qt frameless dialog with QSvgWidget, countdown, Skip/Done buttons.
- `ergonomics/stats.py` — daily breaks, streak, adherence %.
- Storage: reuse `memory.sqlite`; new `breaks` table `(id, ts, type, exercise, completed BOOL)`.
- Sounds: reuse the existing SoundPlayer; add a "break-prompt" cue distinct from success/error/attention (Purr.aiff — soft, non-alarming).

## Wellness disclaimer

The README will state, once, that Claude Pet is **wellness guidance based on published ergonomics research, not medical advice**. It does not treat or diagnose any condition. If you have pain, see a professional.

## What v0.4.0 is NOT

- Not a fitness tracker. No heart rate, no accelerometer, no camera.
- No cloud sync. Break history stays in your local SQLite.
- No gamification with guilt-tripping. Streak = optional badge, never negative.
- Not medical advice. Not a substitute for a workplace ergonomic assessment.
