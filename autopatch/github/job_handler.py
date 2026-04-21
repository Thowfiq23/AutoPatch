"""
autopatch/github/job_handler.py
---------------------------------
Job handler: webhook event → repo_runner → orchestrator → pr_opener.

Called as a FastAPI BackgroundTask from webhook.py.
Never raises — logs all errors and exits cleanly.

Job lifecycle:
  1. Clone repo + run pytest (repo_runner)
  2. If tests pass: log "no action needed", exit
  3. If tests fail: run orchestrator in real-repo mode
  4. If score >= MIN_SCORE_TO_OPEN_PR: open a PR via pr_opener
  5. Store result in memory, clean up workspace
"""

import logging
import os
import shutil
import uuid

from autopatch.agents import memory
from autopatch.github.repo_runner import clone_and_test

logger = logging.getLogger(__name__)

_MIN_SCORE = float(os.getenv("MIN_SCORE_TO_OPEN_PR", "0.5"))

# Registry of recent jobs: {run_id: {repo, branch, status, score, pr_url}}
_recent_jobs: dict = {}


def get_recent_jobs() -> list:
    """Return list of recent job dicts for the /jobs endpoint."""
    return list(_recent_jobs.values())


async def handle_push(payload: dict) -> None:
    repo = payload.get("repository", {}).get("full_name", "unknown")
    branch = payload.get("ref", "refs/heads/main").replace("refs/heads/", "")
    pusher = payload.get("pusher", {}).get("name", "unknown")

    if branch.startswith("autopatch/"):
        logger.info("[JOB] Skipping AutoPatch's own push on %s branch=%s", repo, branch)
        return

    logger.info("[JOB] Push received: %s branch=%s pusher=%s", repo, branch, pusher)
    await _run_fix_job(repo=repo, branch=branch, pr_number=None)


async def handle_pull_request(payload: dict) -> None:
    repo = payload.get("repository", {}).get("full_name", "unknown")
    branch = payload.get("pull_request", {}).get("head", {}).get("ref", "main")
    pr_number = payload.get("number")

    if branch.startswith("autopatch/"):
        logger.info("[JOB] Skipping AutoPatch's own PR on %s branch=%s", repo, branch)
        return

    logger.info("[JOB] PR received: %s #%s branch=%s", repo, pr_number, branch)
    await _run_fix_job(repo=repo, branch=branch, pr_number=pr_number)


async def _run_fix_job(repo: str, branch: str, pr_number) -> None:
    run_id = str(uuid.uuid4())[:8]
    workspace = None

    _recent_jobs[run_id] = {
        "run_id": run_id,
        "repo": repo,
        "branch": branch,
        "pr_number": pr_number,
        "status": "running",
        "score": None,
        "pr_url": None,
    }

    try:
        memory.store_log(run_id, f"[JOB] Starting fix job for {repo} branch={branch} run_id={run_id}")

        # Step 1: Clone and run tests
        try:
            obs = clone_and_test(repo_full_name=repo, branch=branch)
        except RuntimeError as exc:
            memory.store_log(run_id, f"[JOB] Clone/test failed: {exc}")
            _recent_jobs[run_id]["status"] = "error"
            return

        workspace = obs.get("workspace_dir")
        pytest_output = obs.get("action_result", "")

        # Step 2: Check if tests already pass
        output_lower = pytest_output.lower()
        has_failures = "failed" in output_lower or "error" in output_lower
        has_tests = "passed" in output_lower or "failed" in output_lower

        if not has_tests:
            memory.store_log(run_id, "[JOB] No tests found — skipping")
            _recent_jobs[run_id]["status"] = "skipped"
            return

        if not has_failures:
            memory.store_log(run_id, "[JOB] All tests pass — no action needed")
            _recent_jobs[run_id]["status"] = "no_action"
            return

        memory.store_log(run_id, "[JOB] Tests failing — running AutoPatch fix pipeline")

        # Step 3: Run orchestrator in real-repo mode
        from autopatch import orchestrator

        score = await _run_in_thread(
            orchestrator.run_episode,
            run_id=run_id,
            episode=1,
            github_context={
                "repo": repo,
                "issue_number": pr_number or 0,
                "base_branch": branch,
                "workspace_dir": workspace,
                "context": obs.get("context", ""),
            },
        )

        memory.store_log(run_id, f"[JOB] Fix complete score={score:.3f}")
        _recent_jobs[run_id]["score"] = round(score, 3)

        # Step 4: Open PR if score is good enough
        if score >= _MIN_SCORE:
            try:
                from autopatch.github.pr_opener import open_pr
                applied = memory.get_trajectories(run_id)
                pr_url = open_pr(
                    repo=repo,
                    issue_number=pr_number or 0,
                    patched_files=[],
                    final_score=score,
                    rewards=[score],
                    base_branch=branch,
                )
                memory.store_log(run_id, f"[JOB] PR opened: {pr_url}")
                _recent_jobs[run_id]["pr_url"] = pr_url
            except Exception as exc:
                memory.store_log(run_id, f"[JOB] PR open failed: {exc}")

        _recent_jobs[run_id]["status"] = "done"

    except Exception as exc:
        logger.error("[JOB] Failed for %s: %s", repo, exc)
        memory.store_log(run_id, f"[JOB] ERROR: {exc}")
        _recent_jobs[run_id]["status"] = "error"
    finally:
        if workspace:
            shutil.rmtree(workspace, ignore_errors=True)


async def _run_in_thread(fn, **kwargs):
    import asyncio
    return await asyncio.to_thread(fn, **kwargs)
