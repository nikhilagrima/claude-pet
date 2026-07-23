"""Fitness Coach — daily nudges, weekly plan, weight/workout/meal tracking.

Mirrors the ergonomics/ module: data-only plan catalog, config with the same
load/merge pattern, SQLite tracker beside the pet's memory DB, scheduler polled
from the pet's Qt animation timer. No background threads, no daemons.

Intelligence flows through Claude Code sessions via the pet's existing
SessionStart context-injection hook — the pet NEVER calls Anthropic APIs
directly and never uses an API key. When a weekly adjustment is due,
context.py injects a compact block asking Claude Code to write a coaching
note to ~/.claude/claude-pet/fitness_note.txt which the pet then shows once.

NOT medical advice. Weight-loss guidance based on published macros / evidence
(Mifflin-St Jeor BMR, 500 kcal deficit, 1.6–2.0 g/kg protein). Confirm any
supplement decisions with a doctor.
"""
