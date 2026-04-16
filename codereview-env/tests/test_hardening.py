"""
Anti-exploit and reward-hacking tests for CodeReview-Env.

These tests verify that the sandbox correctly blocks all known grader-bypass
and reward-farming exploits so judges can trust the scores they observe.

Run from repo root:
    pytest tests/test_hardening.py -v
"""
import os
import sys
import tempfile
import pytest

# Ensure server package and root are importable regardless of cwd
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "server"))

# Point WORKSPACE_BASE at the platform temp dir before importing environment
# so the default /tmp/codereview_workspaces path is not assumed.  This makes
# the hardening tests pass on Windows and any host where /tmp is not writable.
if "WORKSPACE_BASE" not in os.environ:
    os.environ["WORKSPACE_BASE"] = os.path.join(tempfile.gettempdir(), "codereview_workspaces")

from server.environment import CodeReviewEnvironment
from models import AgentAction


@pytest.fixture
def env():
    """Fresh environment reset to task_1_pr for each test."""
    e = CodeReviewEnvironment()
    e.reset(task_id="task_1_pr")
    yield e
    e.cleanup()


# ---------------------------------------------------------------------------
# Shell-based exploit: root-level conftest.py
# ---------------------------------------------------------------------------

def test_root_conftest_removed_after_exec(env):
    """An agent creating workspace-root conftest.py via shell must have it auto-removed."""
    env.step(AgentAction(
        action_type="execute_command",
        command=(
            "printf 'import pytest\\n"
            "@pytest.fixture(autouse=True)\\n"
            "def _pass_all(monkeypatch): pass\\n' > conftest.py"
        )
    ))
    assert not os.path.exists(os.path.join(env.workspace_dir, "conftest.py")), (
        "EXPLOIT: workspace-root conftest.py was not removed after execute_command"
    )


def test_root_pytest_ini_removed_after_exec(env):
    """pytest.ini at workspace root must be auto-removed after any shell command."""
    env.step(AgentAction(
        action_type="execute_command",
        command="printf '[pytest]\\ntestpaths = .\\n' > pytest.ini"
    ))
    assert not os.path.exists(os.path.join(env.workspace_dir, "pytest.ini")), (
        "EXPLOIT: workspace-root pytest.ini was not removed after execute_command"
    )


def test_root_pyproject_toml_removed_after_exec(env):
    """pyproject.toml created by agent at workspace root must be auto-removed."""
    env.step(AgentAction(
        action_type="execute_command",
        command="printf '[tool.pytest.ini_options]\\ntestpaths=[\".\"]\\n' > pyproject.toml"
    ))
    assert not os.path.exists(os.path.join(env.workspace_dir, "pyproject.toml")), (
        "EXPLOIT: workspace-root pyproject.toml was not removed after execute_command"
    )


def test_sitecustomize_removed_after_exec(env):
    """sitecustomize.py created in workspace root must be auto-removed."""
    env.step(AgentAction(
        action_type="execute_command",
        command="echo 'pass' > sitecustomize.py"
    ))
    assert not os.path.exists(os.path.join(env.workspace_dir, "sitecustomize.py")), (
        "EXPLOIT: sitecustomize.py was not removed after execute_command"
    )


# ---------------------------------------------------------------------------
# patch_file-based exploit: control files via direct write
# ---------------------------------------------------------------------------

def test_patch_file_blocks_root_conftest(env):
    """patch_file must refuse to create conftest.py and issue a -0.05 penalty."""
    _, reward, _, _ = env.step(AgentAction(
        action_type="patch_file",
        target_file="conftest.py",
        new_content="import pytest\n"
    ))
    assert reward == -0.05, (
        f"EXPLOIT: patch_file conftest.py gave reward {reward}, expected -0.05"
    )
    assert not os.path.exists(os.path.join(env.workspace_dir, "conftest.py")), (
        "EXPLOIT: conftest.py was created via patch_file despite security guard"
    )


def test_patch_file_blocks_pytest_ini(env):
    """patch_file must refuse to create pytest.ini."""
    _, reward, _, _ = env.step(AgentAction(
        action_type="patch_file",
        target_file="pytest.ini",
        new_content="[pytest]\n"
    ))
    assert reward == -0.05, (
        f"EXPLOIT: patch_file pytest.ini gave reward {reward}, expected -0.05"
    )
    assert not os.path.exists(os.path.join(env.workspace_dir, "pytest.ini")), (
        "EXPLOIT: pytest.ini was created via patch_file"
    )


# ---------------------------------------------------------------------------
# Existing hardening: test file tampering and path traversal
# ---------------------------------------------------------------------------

def test_test_file_restored_after_shell_chmod(env):
    """Test files must be restored even if the agent does chmod +w then overwrites."""
    # Find the actual test file for the current task (factory tasks use different names)
    tests_dir = os.path.join(env.workspace_dir, "tests")
    test_files = [f for f in os.listdir(tests_dir) if f.endswith(".py")]
    assert test_files, "No test files found in workspace tests/ directory"
    test_fname = test_files[0]
    test_path = os.path.join(tests_dir, test_fname)
    original_content = open(test_path, encoding="utf-8").read()

    env.step(AgentAction(
        action_type="execute_command",
        command=(
            f"chmod +w tests/{test_fname} && "
            f"echo 'def test_always_pass(): pass' > tests/{test_fname}"
        )
    ))

    restored = open(test_path, encoding="utf-8").read()
    assert restored == original_content, (
        "EXPLOIT: test file was modified via chmod+write and NOT restored"
    )


def test_extra_test_file_removed_after_exec(env):
    """Extra files created inside tests/ must be swept after execute_command."""
    env.step(AgentAction(
        action_type="execute_command",
        command="chmod +w tests && echo 'def test_always_pass(): pass' > tests/injected.py"
    ))
    injected = os.path.join(env.workspace_dir, "tests", "injected.py")
    assert not os.path.exists(injected), (
        "EXPLOIT: extra file tests/injected.py was not removed after execute_command"
    )


def test_path_traversal_blocked(env):
    """patch_file must reject paths outside the workspace."""
    _, reward, _, _ = env.step(AgentAction(
        action_type="patch_file",
        target_file="../../../tmp/evil.py",
        new_content="print('escaped')\n"
    ))
    assert reward == -0.05, (
        f"Security: path traversal must yield -0.05 penalty, got {reward}"
    )


def test_direct_test_patch_blocked(env):
    """patch_file must reject writes to the tests/ directory."""
    # Use any test file name — the guard blocks the whole tests/ dir regardless
    tests_dir = os.path.join(env.workspace_dir, "tests")
    test_files = [f for f in os.listdir(tests_dir) if f.endswith(".py")]
    target = f"tests/{test_files[0]}" if test_files else "tests/test_dummy.py"
    _, reward, _, _ = env.step(AgentAction(
        action_type="patch_file",
        target_file=target,
        new_content="def test_always_pass(): pass\n"
    ))
    assert reward == -0.05, (
        f"Security: test file tampering via patch_file must yield -0.05, got {reward}"
    )


# ---------------------------------------------------------------------------
# Reward farming: repeated patches at same pass rate give no extra reward
# ---------------------------------------------------------------------------

def test_no_reward_farming_on_repeated_patch(env):
    """Re-patching with the same content after earning delta reward must not re-award."""
    good_content = (
        "def get_user_query(username):\n"
        "    return ('SELECT * FROM users WHERE username = %s', (username,))\n"
    )
    _, r1, _, _ = env.step(AgentAction(
        action_type="patch_file",
        target_file="auth/models.py",
        new_content=good_content
    ))
    _, r2, _, _ = env.step(AgentAction(
        action_type="patch_file",
        target_file="auth/models.py",
        new_content=good_content
    ))
    assert r2 <= 0.01, (
        f"Reward farming: second identical patch gave r2={r2}, expected <= 0.01"
    )


# ---------------------------------------------------------------------------
# Deterministic grader: verify GRADERS dispatch table is complete
# ---------------------------------------------------------------------------

def test_all_tasks_have_graders():
    """Every task in TASKS must have a corresponding entry in GRADERS."""
    from tasks import TASKS, GRADERS
    for task in TASKS:
        tid = task["task_id"]
        assert tid in GRADERS, f"Missing grader for task {tid}"
        assert callable(GRADERS[tid]), f"GRADERS[{tid!r}] is not callable"


def test_graders_return_float_in_range():
    """Graders must return a float in [0.0, 1.0] even on empty workspace."""
    import tempfile
    from tasks import GRADERS
    with tempfile.TemporaryDirectory() as tmp:
        os.makedirs(os.path.join(tmp, "tests"), exist_ok=True)
        for tid, grader in GRADERS.items():
            result = grader(tmp)
            assert isinstance(result, float), f"{tid}: grader must return float"
            assert 0.0 <= result <= 1.0, f"{tid}: grader returned {result} outside [0.0, 1.0]"
