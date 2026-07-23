"""Personal reminders — 3-stage firing (day-before, 5-min-before, on-time).

Same architecture as fitness/ and ergonomics/: SQLite tracker at
~/.claude/claude-pet/reminders.db, scheduler polled from the pet's Qt tick
loop, no background threads. Reminders can be added via CLI
(`claude-pet remind add "text" --at "..."`) or the REMINDERS dashboard tab.

Each reminder can fire up to 3 times:
  - "day_before"  when now >= due − 24h and not yet fired
  - "five_min"    when now >= due − 5 min and not yet fired
  - "on_time"     when now >= due and not yet fired
Once all three have fired the reminder is auto-marked completed.
"""
