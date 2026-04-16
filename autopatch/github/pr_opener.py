"""
autopatch/github/pr_opener.py
------------------------------
Opens a pull request with AutoPatch's fixes when an episode achieves
reward = 1.0 (perfect score).

GUARD: This module must only be called when max(rewards) == 1.0.
The orchestrator enforces this — never call open_pr() for partial episodes.

Part of the v2 GitHub integration.

Usage:
    from autopatch.github.pr_opener import open_pr
    pr_url = open_pr(
        repo="owner/repo",
        issue_number=42,
        patched_files=[{"path": "auth/crypto.py", "content": "..."}],
        final_score=1.0,
        rewards=[0.0, 0.45, 0.9, 1.0],
    )
"""

import logging
import os
from typing import List

logger = logging.getLogger(__name__)

# Link shown in every PR body
_AUTOPATCH_REPO = "https://github.com/your-username/autopatch"


def open_pr(
    repo: str,
    issue_number: int,
    patched_files: List[dict],
    final_score: float,
    rewards: List[float],
    base_branch: str = "main",
) -> str:
    """
    Commit all patched files to a new branch and open a pull request.

    Parameters
    ----------
    repo : str
        Repository in "owner/repo" format.
    issue_number : int
        The issue being fixed — used in the branch name.
    patched_files : list of dict
        Each entry: {"path": "relative/path.py", "content": "...full file..."}
    final_score : float
        Must be 1.0. Caller (orchestrator) enforces this guard.
    rewards : list of float
        Full reward trajectory for this episode.
    base_branch : str
        Branch to open the PR against (default: "main").

    Returns
    -------
    str
        The HTML URL of the newly created pull request.

    Raises
    ------
    EnvironmentError
        If GITHUB_TOKEN is not set.
    ValueError
        If final_score != 1.0 (guard violation).
    RuntimeError
        On any GitHub API error.
    """
    # ── Guard ─────────────────────────────────────────────────────────────────
    if final_score < 1.0:
        raise ValueError(
            f"open_pr() called with final_score={final_score:.3f} — "
            "PRs are only opened for perfect episodes (score == 1.0)."
        )

    # ── Auth ──────────────────────────────────────────────────────────────────
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        raise EnvironmentError(
            "GITHUB_TOKEN environment variable is required to open pull requests."
        )

    try:
        from github import Github, GithubException

        gh        = Github(token)
        gh_repo   = gh.get_repo(repo)

        # ── Branch creation ───────────────────────────────────────────────────
        branch_name = f"autopatch/fix-{issue_number}"
        base_sha    = gh_repo.get_branch(base_branch).commit.sha

        try:
            gh_repo.create_git_ref(
                ref=f"refs/heads/{branch_name}",
                sha=base_sha,
            )
            logger.info("[PR] Created branch %s on %s", branch_name, repo)
        except GithubException as exc:
            # 422 = branch already exists — idempotent, continue without error
            if exc.status == 422:
                logger.info("[PR] Branch %s already exists — reusing", branch_name)
            else:
                raise RuntimeError(
                    f"Failed to create branch '{branch_name}' on {repo}: {exc}"
                ) from exc

        # ── Commit each patched file ──────────────────────────────────────────
        for pf in patched_files:
            file_path   = pf.get("path", "")
            new_content = pf.get("content", "")
            if not file_path or not new_content:
                continue

            commit_msg = f"fix: AutoPatch patch for issue #{issue_number} — {file_path}"

            try:
                # Try to update existing file
                existing = gh_repo.get_contents(file_path, ref=branch_name)
                gh_repo.update_file(
                    path=file_path,
                    message=commit_msg,
                    content=new_content,
                    sha=existing.sha,
                    branch=branch_name,
                )
                logger.info("[PR] Updated %s on branch %s", file_path, branch_name)

            except GithubException as exc:
                if exc.status == 404:
                    # File doesn't exist yet — create it
                    gh_repo.create_file(
                        path=file_path,
                        message=commit_msg,
                        content=new_content,
                        branch=branch_name,
                    )
                    logger.info("[PR] Created %s on branch %s", file_path, branch_name)
                else:
                    raise RuntimeError(
                        f"Failed to commit {file_path} to branch {branch_name}: {exc}"
                    ) from exc

        # ── Build PR body ─────────────────────────────────────────────────────
        pr_body = _build_pr_body(
            issue_number=issue_number,
            repo=repo,
            patched_files=patched_files,
            final_score=final_score,
            rewards=rewards,
        )

        # ── Open pull request ─────────────────────────────────────────────────
        pr = gh_repo.create_pull(
            title=f"[AutoPatch] Fix issue #{issue_number}",
            body=pr_body,
            head=branch_name,
            base=base_branch,
        )

        pr_url = pr.html_url
        logger.info("[PR] Opened PR: %s", pr_url)
        return pr_url

    except (EnvironmentError, ValueError, RuntimeError):
        raise
    except Exception as exc:
        raise RuntimeError(f"Unexpected error opening PR for issue #{issue_number}: {exc}") from exc


# ─── PR body builder ──────────────────────────────────────────────────────────

def _build_pr_body(
    issue_number: int,
    repo: str,
    patched_files: List[dict],
    final_score: float,
    rewards: List[float],
) -> str:
    """Format a human-readable PR description showing what the agents did."""

    # Reward trajectory: "0.00 → 0.45 → 0.90 → 1.00"
    trajectory = " → ".join(f"{r:.2f}" for r in rewards) if rewards else "1.00"

    files_section = "\n".join(
        f"- `{pf.get('path', 'unknown')}`" for pf in patched_files
    ) or "- *(no files)*"

    return f"""## 🤖 AutoPatch — Automated Fix for Issue #{issue_number}

This pull request was generated automatically by **AutoPatch**, a self-improving
multi-agent AI system that detects, patches, and validates Python bugs.

### Agent System

| Agent | Role |
|-------|------|
| **Planner** | Analysed pytest failures and created a structured fix plan |
| **Coder** | Generated complete corrected files for each identified bug |
| **Critic** | Validated each patch against security rules before applying |
| **Memory** | Retrieved successful patterns from past episodes |
| **Evolver** | Continuously improves the Coder's prompts based on reward history |

### Results

| Metric | Value |
|--------|-------|
| **Final Score** | `{final_score:.3f}` / 1.000 |
| **Reward Trajectory** | `{trajectory}` |
| **Files Patched** | {len(patched_files)} |

### Files Changed

{files_section}

### How it works

AutoPatch runs in a reinforcement learning loop against a sandboxed environment.
Each episode the agents attempt to fix all failing tests. The Evolver agent
analyses reward trajectories and rewrites the Coder's system prompt to improve
performance over time.

---

*Generated by [AutoPatch]({_AUTOPATCH_REPO}) · LangGraph · Groq llama-3.3-70b-versatile*
*Closes #{issue_number}*
"""
