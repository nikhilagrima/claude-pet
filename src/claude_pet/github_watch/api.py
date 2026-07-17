"""Minimal GitHub REST client for the watcher.

One endpoint (`/repos/{owner}/{repo}/events`), ETag caching, PAT-optional.
All requests use a 5-second timeout and never follow surprise redirects.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import requests

from .. import __version__
from . import config


BASE = "https://api.github.com"


@dataclass
class PollResult:
    status: int                    # HTTP status
    events: list[dict[str, Any]]   # empty on 304 or error
    etag: str | None               # server-returned ETag (may be None)
    rate_remaining: int | None
    rate_reset_ts: int | None
    error: str | None              # human-readable message if non-transient


def _headers(etag: str | None) -> dict[str, str]:
    h = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": f"claude-pet/{__version__}",
    }
    tok = config.token()
    if tok:
        h["Authorization"] = f"Bearer {tok}"
    if etag:
        h["If-None-Match"] = etag
    return h


def _rate_info(resp: requests.Response) -> tuple[int | None, int | None]:
    def _int(name: str) -> int | None:
        v = resp.headers.get(name)
        try:
            return int(v) if v is not None else None
        except ValueError:
            return None
    return _int("X-RateLimit-Remaining"), _int("X-RateLimit-Reset")


def poll_repo(
    owner: str,
    repo: str,
    etag: str | None = None,
    *,
    session: requests.Session | None = None,
) -> PollResult:
    """Fetch recent events for a repo. 304 → empty list, no error.

    Non-transient errors (404/401/403 non-rate) are reported via `error` so the
    caller can disable the watch. Transient errors (timeout, DNS, 5xx) return
    `error=None` and empty events — the poller silently retries next tick.
    """
    url = f"{BASE}/repos/{owner}/{repo}/events?per_page=30"
    s = session or requests
    try:
        # allow_redirects=True: GitHub returns 301 permanent-redirect for
        # renamed repos (e.g. anthropics/anthropic-cookbook → anthropics/cookbook)
        # and the redirect target is the correct api.github.com URL. Following
        # is safe here since we only ever hit api.github.com.
        resp = s.get(url, headers=_headers(etag), timeout=5.0, allow_redirects=True)
    except requests.RequestException:
        return PollResult(0, [], etag, None, None, None)

    remaining, reset = _rate_info(resp)
    new_etag = resp.headers.get("ETag") or etag

    if resp.status_code == 304:
        return PollResult(304, [], new_etag, remaining, reset, None)

    if resp.status_code == 200:
        try:
            data = resp.json()
        except ValueError:
            return PollResult(200, [], new_etag, remaining, reset, None)
        if not isinstance(data, list):
            return PollResult(200, [], new_etag, remaining, reset, None)
        return PollResult(200, data, new_etag, remaining, reset, None)

    if resp.status_code == 404:
        return PollResult(404, [], etag, remaining, reset,
                          "repo not found or private (add a token?)")
    if resp.status_code == 401:
        return PollResult(401, [], etag, remaining, reset,
                          "token invalid or expired")
    if resp.status_code == 403:
        # 403 can mean rate-limited (transient, no error) or forbidden (persistent)
        if remaining == 0:
            return PollResult(403, [], etag, remaining, reset, None)
        return PollResult(403, [], etag, remaining, reset, "access forbidden")
    if 500 <= resp.status_code < 600:
        return PollResult(resp.status_code, [], etag, remaining, reset, None)

    return PollResult(resp.status_code, [], etag, remaining, reset,
                      f"unexpected status {resp.status_code}")
