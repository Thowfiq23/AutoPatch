"""
autopatch/github/ingester.py
-----------------------------
Fetches a real GitHub issue and converts it into a CodeReview-Env compatible
task observation dict that can be passed directly to planner.plan().

Part of the v2 GitHub integration. Build and use this AFTER the core system
is working end-to-end.

Usage:
    from autopatch.github.ingester import fetch_issue
    obs = fetch_issue("https://github.com/owner/repo/issues/42")
    plan = planner.plan(obs)
"""

import os
import re
from typing import List


# ── Regex: parse GitHub issue URL ────────────────────────────────────────────
_ISSUE_URL_RE = re.compile(
    r'^https?://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/issues/(?P<number>\d+)/?$'
)

# ── Regex: extract `filename.py` mentions from issue body ────────────────────
_FILE_MENTION_RE = re.compile(r'`([^`]+\.py)`')


def fetch_issue(url: str) -> dict:
    """
    Fetch a GitHub issue and return a CodeObservation-compatible dict.

    Parameters
    ----------
    url : str
        Full GitHub issue URL, e.g. https://github.com/owner/repo/issues/42

    Returns
    -------
    dict
        {
          issue_number:    int,
          title:           str,
          context:         str   — full issue body (passed to planner as context),
          available_files: list  — .py filenames extracted from backtick mentions,
          repo:            str   — "owner/repo",
          url:             str   — original URL,
          action_result:   str   — empty string (no pytest output yet),
        }

    Raises
    ------
    ValueError
        If the URL does not match the expected GitHub issue pattern.
    EnvironmentError
        If GITHUB_TOKEN is not set.
    RuntimeError
        If the GitHub API rate limit is exceeded (includes wait time in message).
    """
    # ── URL parsing ───────────────────────────────────────────────────────────
    m = _ISSUE_URL_RE.match(url.strip())
    if not m:
        raise ValueError(
            f"Invalid GitHub issue URL — expected "
            f"https://github.com/owner/repo/issues/N, got: {url!r}"
        )

    owner        = m.group("owner")
    repo_name    = m.group("repo")
    issue_number = int(m.group("number"))
    full_repo    = f"{owner}/{repo_name}"

    # ── Auth ──────────────────────────────────────────────────────────────────
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        raise EnvironmentError(
            "GITHUB_TOKEN environment variable is required for GitHub integration. "
            "Create a personal access token with 'repo' scope at "
            "https://github.com/settings/tokens and add it to your .env file."
        )

    # ── Fetch issue ───────────────────────────────────────────────────────────
    try:
        from github import Github, RateLimitExceededException, UnknownObjectException

        gh   = Github(token)
        repo = gh.get_repo(full_repo)

        try:
            issue = repo.get_issue(number=issue_number)
        except UnknownObjectException:
            raise ValueError(
                f"Issue #{issue_number} not found in {full_repo}. "
                "Check the URL and ensure the token has access to this repository."
            )

        body  = issue.body or ""
        title = issue.title or f"Issue #{issue_number}"

        # ── Extract file mentions ─────────────────────────────────────────────
        available_files = _extract_files(body, repo, issue)

        # ── Build context string ──────────────────────────────────────────────
        context = (
            f"GitHub Issue #{issue_number}: {title}\n"
            f"Repository: {full_repo}\n"
            f"URL: {url}\n\n"
            f"{body}"
        )

        return {
            "issue_number":   issue_number,
            "title":          title,
            "context":        context,
            "available_files": available_files,
            "repo":           full_repo,
            "url":            url,
            # Planner reads these fields from CodeObservation
            "action_result":  "",   # no pytest output at ingestion time
            "task_id":        f"github-{full_repo.replace('/', '-')}-{issue_number}",
            "step_number":    0,
            "done":           False,
            "reward":         0.0,
        }

    except RateLimitExceededException as exc:
        import time as _time
        # PyGithub stores reset timestamp in the exception's data
        reset_ts = getattr(exc, "reset_timestamp", None)
        if reset_ts:
            wait_secs = max(0, int(reset_ts - _time.time()))
            raise RuntimeError(
                f"GitHub API rate limit exceeded. "
                f"Limit resets in {wait_secs} seconds ({wait_secs // 60}m {wait_secs % 60}s). "
                "Consider authenticating with a GitHub token to increase your limit."
            ) from exc
        raise RuntimeError(
            "GitHub API rate limit exceeded. Wait a few minutes and try again."
        ) from exc


def _extract_files(body: str, repo=None, issue=None) -> List[str]:
    """
    Extract Python file mentions from an issue body.

    First looks for backtick-quoted names like `auth/crypto.py`.
    Falls back to scanning issue comments if the body mentions few files.

    Always returns a list (never None, never raises).
    """
    files: List[str] = []

    # Backtick mentions in body
    files.extend(_FILE_MENTION_RE.findall(body or ""))

    # Also scan comments for additional file mentions
    if repo is not None and issue is not None:
        try:
            for comment in issue.get_comments():
                files.extend(_FILE_MENTION_RE.findall(comment.body or ""))
        except Exception:
            pass  # comments are best-effort

    # Deduplicate while preserving order
    seen  = set()
    deduped = []
    for f in files:
        if f not in seen:
            seen.add(f)
            deduped.append(f)

    return deduped
