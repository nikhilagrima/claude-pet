# GitHub Repository Watcher — API Research & Design

Feature: let the developer add GitHub repos to a watch list. The pet polls in the background and alerts (visual reaction + sound) when new activity happens — commits, PRs, PR reviews, releases, deploys/workflow runs.

## Requirements

- Works with **public repos, no authentication** (the common case).
- **Optional GitHub PAT** for private repos and higher rate limits.
- **No webhooks** — the pet is a local desktop app, no public HTTPS endpoint. Polling only.
- **No re-alerts** — an event that was already reported once must never trigger the pet again.
- **UI + CLI** for add/remove/list/enable-disable.
- **Zero credentials in the repo.** Any PAT the user supplies goes to `~/.claude/claude-pet/config.json`, never committed.

## GitHub REST API endpoints considered

| Endpoint | What it gives | Rate cost | Chosen? |
|---|---|---|---|
| `GET /repos/{owner}/{repo}/events` | Recent public events (Push, PR, Review, Release, IssueComment, Watch, Create, Delete, Fork, Deployment, etc.) — up to 300, last 90 days | 1 req | **Yes — primary** |
| `GET /repos/{owner}/{repo}/commits?since={iso}` | Commits on default branch | 1 req | No (events covers PushEvent) |
| `GET /repos/{owner}/{repo}/pulls?state=all&sort=updated` | PRs (opened, updated, merged) | 1 req | No (events covers PullRequestEvent) |
| `GET /repos/{owner}/{repo}/pulls/{n}/reviews` | Per-PR reviews | 1 req per PR | No (events covers PullRequestReviewEvent) |
| `GET /repos/{owner}/{repo}/releases` | Releases | 1 req | No (events covers ReleaseEvent) |
| `GET /repos/{owner}/{repo}/actions/runs?per_page=10` | CI/deploy runs — status/conclusion | 1 req | **Yes — supplement** (events omits successful workflow_run for private repos and public repos w/ many events) |
| `GET /notifications` | User's aggregated notification stream | 1 req | No (requires auth, needs `notifications` scope, mutates read-state) |

### Why `/events` as the primary source

One endpoint, one request per repo, covers every activity type we care about. Each event has:

- `id` — monotonic string; use as the dedup cursor.
- `type` — event kind (see table below).
- `actor` — GitHub user who triggered it.
- `created_at` — ISO timestamp.
- `payload` — event-specific detail (commits, PR object, review, etc.).
- `repo` — echoes the target repo.

### Event types → pet reaction

| GitHub event | Pet emotion | Sound | Summary shown |
|---|---|---|---|
| `PushEvent` | curious | attention beeps | "N new commits by @user on branch X" |
| `PullRequestEvent` (opened) | curious | attention | "PR #N opened by @user: <title>" |
| `PullRequestEvent` (closed, merged=true) | success | done | "PR #N merged" |
| `PullRequestEvent` (closed, merged=false) | error | error thud | "PR #N closed without merge" |
| `PullRequestReviewEvent` (approved) | success | done | "@user approved PR #N" |
| `PullRequestReviewEvent` (changes_requested) | error | error thud | "@user requested changes on PR #N" |
| `PullRequestReviewEvent` (commented) | curious | attention | "@user reviewed PR #N" |
| `ReleaseEvent` (published) | success | done | "Release <tag> published" |
| `IssuesEvent` (opened) | curious | attention | "Issue #N opened: <title>" |
| `WorkflowRunEvent` (completed, success) | success | done | "CI: <workflow> passed" |
| `WorkflowRunEvent` (completed, failure) | error | error thud | "CI: <workflow> failed" |
| `DeploymentStatusEvent` (success) | success | done | "Deploy to <env> succeeded" |
| `DeploymentStatusEvent` (failure/error) | error | error thud | "Deploy to <env> failed" |

Other event types (Watch, Fork, Create-branch, Delete-branch, MemberEvent, etc.) are stored but do NOT trigger the pet — quiet-mode by design so a busy public repo doesn't turn the pet into a distraction.

## Rate limits

| Auth | Limit | Requests/hr per repo at 5min poll |
|---|---|---|
| Unauthenticated (IP) | 60 req/hr | 12 req/hr → **safe for 5 repos** |
| Authenticated PAT | 5000 req/hr | 12 req/hr → **safe for 400 repos** |

### ETag / If-None-Match

GitHub's `/events` endpoint honors `If-None-Match: <etag>`. A 304 response does NOT count against the rate limit. **We cache the etag per repo** and send it on every subsequent poll. On a quiet repo this means 0 rate-limit cost after the first poll — the pet can watch many repos cheaply.

### Backoff

- On `403 rate limit exceeded` → back off until the `X-RateLimit-Reset` timestamp, then resume.
- On `404 Not Found` → mark the watch `disabled` with reason "repo not found or private" and stop polling until the user fixes it.
- On `401 Unauthorized` (only when a token is set) → mark all watches `disabled` with reason "token invalid" and prompt user via a pet notification.
- Network errors → silent, retry next tick.

## Poll cadence

- Default **5 minutes** per repo, configurable via `~/.claude/claude-pet/config.json` → `github.poll_interval_s`.
- Poll runs from the pet's existing `_tick` loop (every 30 frames ≈ 3 seconds we check "is it time to poll any repo yet?").
- Poll happens on a background thread so the UI never blocks.
- First poll after adding a repo: **do not alert on any existing events** — we treat all events at `add` time as "seen" and set the cursor to the newest event id.

## Storage

Add to `memory.sqlite` (schema v3 additive migration):

```sql
CREATE TABLE gh_watches (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  owner         TEXT NOT NULL,
  repo          TEXT NOT NULL,
  enabled       INTEGER NOT NULL DEFAULT 1,
  last_event_id TEXT,             -- dedup cursor
  etag          TEXT,              -- If-None-Match cache
  last_checked  TEXT,              -- ISO
  last_error    TEXT,              -- last non-transient error msg, if any
  added_at      TEXT NOT NULL,
  UNIQUE(owner, repo)
);

CREATE TABLE gh_events (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  watch_id     INTEGER NOT NULL REFERENCES gh_watches(id) ON DELETE CASCADE,
  event_id     TEXT NOT NULL,     -- GitHub's event id, unique per repo
  event_type   TEXT NOT NULL,
  actor        TEXT,
  title        TEXT NOT NULL,     -- human summary, e.g. "PR #42 merged"
  url          TEXT,              -- link to the thing on github.com
  reaction     TEXT NOT NULL,     -- 'curious' | 'success' | 'error' | 'none'
  created_at   TEXT NOT NULL,     -- from the API
  seen_at      TEXT NOT NULL,     -- when the pet noticed it
  alerted      INTEGER NOT NULL DEFAULT 0,
  UNIQUE(watch_id, event_id)
);

CREATE INDEX idx_gh_events_watch ON gh_events(watch_id, seen_at DESC);
```

## Configuration (`~/.claude/claude-pet/config.json` — new `github` key)

```json
{
  "github": {
    "enabled": true,
    "poll_interval_s": 300,
    "token": null,          // optional PAT
    "alert_types": {
      "PushEvent": true,
      "PullRequestEvent": true,
      "PullRequestReviewEvent": true,
      "ReleaseEvent": true,
      "IssuesEvent": true,
      "WorkflowRunEvent": true,
      "DeploymentStatusEvent": true
    }
  }
}
```

Users can silence noisy categories per-repo by editing this file (or the UI, phase 2).

## CLI surface

```
claude-pet github watch <owner/repo>       # add
claude-pet github unwatch <owner/repo>     # remove
claude-pet github list                     # list watches + last-checked + status
claude-pet github events [--limit 20]      # recent events across all watches
claude-pet github check                    # force poll now (blocking)
claude-pet github token <PAT>              # store token in config
claude-pet github token --remove           # clear stored token
```

## UI surface

New **"GitHub"** tab in the HUD panel:

- Add box: `owner/repo` field + `+ Watch` button.
- Table of watched repos with: name, last-checked age, most-recent event type + summary, enable/disable toggle, `⨯` remove.
- Live event feed below the table (last 20 events, colored by reaction).
- Footer: "Poll interval 5m" + "Rate limit: X remaining" (from last response headers).

## Security notes

- The PAT is written only to `~/.claude/claude-pet/config.json` on the user's machine, chmod'd to 0600 on POSIX.
- `distill.redact()` already scrubs `github_pat_*` and `ghp_*` patterns — if a PAT ever leaks into a note or transcript, it's zapped before hitting the memory DB.
- No PAT ever sent anywhere except `https://api.github.com` with `Authorization: Bearer <token>`.
- All GitHub network I/O uses `requests` (already a dependency), 5-second timeout, no redirect surprises.
