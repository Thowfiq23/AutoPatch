"""
autopatch/github/repo_runner.py
--------------------------------
Clone a real GitHub repo, run pytest, and return an observation dict
compatible with planner.plan().

The workspace directory is returned to the caller, who is responsible
for cleanup (shutil.rmtree) after the fix job completes.
"""

import logging
import os
import shutil
import subprocess
import tempfile
from typing import Optional

logger = logging.getLogger(__name__)

_PYTEST_TIMEOUT = int(os.getenv("PYTEST_TIMEOUT", "60"))
_MAX_FILES = int(os.getenv("MAX_FILES_TO_PATCH", "3"))

_SKIP_DIRS = {"__pycache__", ".venv", "venv", ".git", "node_modules", ".tox", "dist", "build"}
_SKIP_FILE_PARTS = {"test_", "_test", "conftest", "setup.py", "setup.cfg"}


def clone_and_test(
    repo_full_name: str,
    branch: str = "main",
    github_token: Optional[str] = None,
) -> dict:
    """
    Clone a GitHub repo, install deps, run pytest, collect .py files.

    Returns
    -------
    dict with keys:
        context        : str  — repo + branch description
        action_result  : str  — full pytest stdout+stderr
        available_files: list — relative .py paths (non-test, non-pycache)
        repo           : str
        branch         : str
        workspace_dir  : str  — caller MUST shutil.rmtree() after done

    Raises
    ------
    RuntimeError if clone or pytest invocation itself fails.
    """
    token = github_token or os.getenv("GITHUB_TOKEN", "")
    if token:
        clone_url = f"https://{token}@github.com/{repo_full_name}.git"
    else:
        clone_url = f"https://github.com/{repo_full_name}.git"

    workspace = tempfile.mkdtemp(prefix="autopatch_")
    logger.info("[RUNNER] Cloning %s branch=%s into %s", repo_full_name, branch, workspace)

    try:
        subprocess.run(
            ["git", "clone", "--depth=1", "--branch", branch, clone_url, workspace],
            check=True, capture_output=True, timeout=60,
        )
        logger.info("[RUNNER] Clone complete")

        # Install deps — try requirements.txt then pyproject/setup.py
        req_path = os.path.join(workspace, "requirements.txt")
        if os.path.isfile(req_path):
            logger.info("[RUNNER] Installing requirements.txt")
            subprocess.run(
                ["pip", "install", "-r", req_path, "-q"],
                capture_output=True, timeout=120, cwd=workspace,
            )
        elif os.path.isfile(os.path.join(workspace, "pyproject.toml")) or \
                os.path.isfile(os.path.join(workspace, "setup.py")):
            logger.info("[RUNNER] Installing package in editable mode")
            subprocess.run(
                ["pip", "install", "-e", ".", "-q"],
                capture_output=True, timeout=120, cwd=workspace,
            )

        # Run pytest
        logger.info("[RUNNER] Running pytest in %s", workspace)
        result = subprocess.run(
            ["pytest", "--tb=short", "-v"],
            capture_output=True, text=True,
            timeout=_PYTEST_TIMEOUT, cwd=workspace,
        )
        pytest_output = result.stdout + result.stderr
        logger.info("[RUNNER] pytest exit_code=%d output_len=%d", result.returncode, len(pytest_output))

    except subprocess.TimeoutExpired as exc:
        shutil.rmtree(workspace, ignore_errors=True)
        raise RuntimeError(f"Timeout cloning/testing {repo_full_name}") from exc
    except subprocess.CalledProcessError as exc:
        shutil.rmtree(workspace, ignore_errors=True)
        stderr = exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        raise RuntimeError(f"Failed to clone {repo_full_name}: {stderr[:300]}") from exc
    except FileNotFoundError as exc:
        shutil.rmtree(workspace, ignore_errors=True)
        raise RuntimeError(f"Required tool not found (git/pytest): {exc}") from exc
    except Exception as exc:
        shutil.rmtree(workspace, ignore_errors=True)
        raise RuntimeError(f"Unexpected error for {repo_full_name}: {exc}") from exc

    # Collect non-test .py files
    available_files = []
    for root, dirs, files in os.walk(workspace):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for f in files:
            if not f.endswith(".py"):
                continue
            if any(part in f for part in _SKIP_FILE_PARTS):
                continue
            rel = os.path.relpath(os.path.join(root, f), workspace).replace("\\", "/")
            # Also skip files under tests/ directories
            if "/tests/" in f"/{rel}" or rel.startswith("tests/"):
                continue
            available_files.append(rel)

    if not pytest_output.strip():
        pytest_output = "No test output captured. Ensure pytest is installed in the repo."

    return {
        "context": (
            f"Repository: {repo_full_name}\n"
            f"Branch: {branch}\n"
            "AutoPatch automatic bug fix run."
        ),
        "action_result": pytest_output,
        "available_files": available_files,
        "repo": repo_full_name,
        "branch": branch,
        "workspace_dir": workspace,
    }
