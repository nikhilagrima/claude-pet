"""GitHub repository watcher.

Polls a user-configured list of repos for new activity (commits, PRs, reviews,
releases, deploys/CI runs) and hands new events to the pet's reaction system.

Zero credentials in the source tree: any GitHub PAT lives only in the user's
`~/.claude/claude-pet/config.json` and is gitignored by convention.
"""
