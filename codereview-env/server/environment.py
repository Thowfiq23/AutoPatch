import json
import os
import re
import uuid
import platform
import subprocess
import shutil
from subprocess import TimeoutExpired
from typing import Dict, Any, Tuple, Optional, List, Set

from models import AgentAction, CodeObservation, ReviewState
from tasks import TASKS, GRADERS

# Configurable via env var.  Falls back to the platform temp directory so the
# default works on Windows, macOS, and Linux without manual configuration.
import tempfile as _tempfile
WORKSPACE_BASE = os.getenv(
    "WORKSPACE_BASE",
    os.path.join(_tempfile.gettempdir(), "codereview_workspaces"),
)

MAX_STEPS = 10

# Minimal clean environment passed to all subprocesses.
_CLEAN_ENV_KEYS = {"PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "USER", "LOGNAME"}
_WINDOWS_ENV_KEYS = {"SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "TEMP", "TMP", "COMSPEC"}

# Pytest/Python control files that can override test discovery.
_PYTEST_CONTROL_NAMES = frozenset({
    "conftest.py", "pytest.ini", "pyproject.toml", "setup.cfg",
    "tox.ini", "sitecustomize.py", ".pytest.ini",
})

# Tasks that include a service component (have app_init.py + state artifacts).
_SERVICE_TASKS = frozenset({"task_7_pr", "task_8_pr", "task_9_pr", "task_10_pr"})


def _make_clean_env(pythonpath: str) -> Dict[str, str]:
    """Return a minimal subprocess environment — no server credentials leaked."""
    keys = _CLEAN_ENV_KEYS.copy()
    if platform.system() == "Windows":
        keys |= _WINDOWS_ENV_KEYS
    clean = {k: v for k, v in os.environ.items() if k in keys}
    clean["PYTHONPATH"] = pythonpath
    clean["PYTHONDONTWRITEBYTECODE"] = "1"
    if "PATH" not in clean:
        clean["PATH"] = "/usr/local/bin:/usr/bin:/bin"
    return clean


def _parse_log_file(path: str) -> List[Dict[str, str]]:
    """Parse a structured log file into a list of {ts, level, msg} dicts."""
    entries = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.rstrip()
                if not line:
                    continue
                # Format: "2026-01-01T00:00:01Z LEVEL message..."
                parts = line.split(" ", 2)
                if len(parts) == 3:
                    entries.append({"ts": parts[0], "level": parts[1], "msg": parts[2]})
                else:
                    entries.append({"ts": "", "level": "INFO", "msg": line})
    except Exception:
        pass
    return entries[-50:]  # keep last 50 entries


class CodeReviewEnvironment:
    def __init__(self):
        if os.path.isdir(WORKSPACE_BASE):
            shutil.rmtree(WORKSPACE_BASE, ignore_errors=True)
        os.makedirs(WORKSPACE_BASE, exist_ok=True)

        self.state = ReviewState(
            episode_id=str(uuid.uuid4()),
            task_id="",
            step_count=0,
            current_task_index=0,
            total_reward=0.0,
            done=False,
            system_health="unknown",
            progress_depth=0,
            trap_count=0,
            optimal_steps=0,
        )
        self.current_task_data = None
        self._is_first_reset = True
        self.workspace_dir = ""
        self.prev_pass_rate: float = 0.0
        self._test_file_originals: Dict[str, str] = {}
        self._original_repo_files: set = set()
        # Multi-component reward tracking
        self._tool_types_used: Set[str] = set()
        self._restart_count: int = 0
        self._prev_health_at_restart: str = "unknown"

    # ─────────────────────────────────────────────────────────────────────────
    # Workspace management
    # ─────────────────────────────────────────────────────────────────────────

    def _setup_workspace(self):
        if self.workspace_dir and os.path.exists(self.workspace_dir):
            shutil.rmtree(self.workspace_dir, ignore_errors=True)

        self.workspace_dir = os.path.join(WORKSPACE_BASE, self.state.episode_id)
        os.makedirs(self.workspace_dir, exist_ok=True)

        self._test_file_originals = {}
        self._original_repo_files = set()

        _repo_factory = self.current_task_data.get("_repo_factory")
        if _repo_factory is not None:
            import hashlib as _hashlib
            _seed = int(_hashlib.sha256(
                self.state.episode_id.encode()
            ).hexdigest()[:8], 16)
            repo = _repo_factory(_seed)
        else:
            repo = self.current_task_data.get("repository", {})

        for filepath, content in repo.items():
            full_path = os.path.join(self.workspace_dir, filepath)
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(content)
            self._original_repo_files.add(filepath)
            if filepath.startswith("tests/"):
                os.chmod(full_path, 0o444)
                self._test_file_originals[filepath] = content

    def cleanup(self):
        if self.workspace_dir and os.path.exists(self.workspace_dir):
            shutil.rmtree(self.workspace_dir, ignore_errors=True)

    def _enforce_test_integrity(self) -> None:
        for relpath, original in self._test_file_originals.items():
            full_path = os.path.join(self.workspace_dir, relpath)
            try:
                with open(full_path, "r", encoding="utf-8") as f:
                    current = f.read()
                if current == original:
                    continue
            except OSError:
                current = None
            try:
                os.chmod(full_path, 0o644)
            except OSError:
                pass
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(original)
            os.chmod(full_path, 0o444)

        tests_dir = os.path.join(self.workspace_dir, "tests")
        if os.path.isdir(tests_dir):
            tracked_abs = {
                os.path.normpath(os.path.join(self.workspace_dir, p))
                for p in self._test_file_originals
            }
            for dirpath, _, filenames in os.walk(tests_dir):
                for fname in filenames:
                    fpath = os.path.normpath(os.path.join(dirpath, fname))
                    if fpath not in tracked_abs:
                        try:
                            os.remove(fpath)
                        except OSError:
                            pass

        for dirpath, _, filenames in os.walk(self.workspace_dir):
            for fname in filenames:
                if fname not in _PYTEST_CONTROL_NAMES:
                    continue
                fpath = os.path.normpath(os.path.join(dirpath, fname))
                relpath = os.path.relpath(fpath, self.workspace_dir).replace(os.sep, "/")
                if relpath not in self._original_repo_files:
                    try:
                        os.remove(fpath)
                    except OSError:
                        pass

    # ─────────────────────────────────────────────────────────────────────────
    # System health detection (for incident tasks 7-10)
    # ─────────────────────────────────────────────────────────────────────────

    def _detect_system_health(self) -> str:
        """Infer system health from workspace artifacts."""
        task_id = self.state.task_id

        if task_id in ("task_7_pr", "task_9_pr"):
            state_path = os.path.join(self.workspace_dir, "service_state.json")
            if os.path.isfile(state_path):
                try:
                    with open(state_path, encoding="utf-8") as f:
                        st = json.load(f)
                    return st.get("status", "unknown")
                except Exception:
                    return "unknown"
            return "crashed"

        if task_id == "task_8_pr":
            db_path = os.path.join(self.workspace_dir, "app.db")
            if not os.path.isfile(db_path):
                return "crashed"
            try:
                import sqlite3
                conn = sqlite3.connect(db_path)
                cur = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name NOT LIKE 'sqlite_%'"
                )
                tables = {r[0] for r in cur.fetchall()}
                cur2 = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='index' "
                    "AND name NOT LIKE 'sqlite_%'"
                )
                indexes = {r[0] for r in cur2.fetchall()}
                conn.close()
                if "users" in tables and "posts" in tables and len(indexes) >= 2:
                    return "healthy"
                if tables:
                    return "degraded"
                return "crashed"
            except Exception:
                return "unknown"

        if task_id == "task_10_pr":
            state_path = os.path.join(self.workspace_dir, "service_state.json")
            if os.path.isfile(state_path):
                try:
                    with open(state_path, encoding="utf-8") as f:
                        topo = json.load(f)
                    services = topo.get("services", {})
                    if not services:
                        return "unknown"
                    statuses = [svc.get("status", "crashed") for svc in services.values()]
                    if all(s in ("running", "healthy") for s in statuses):
                        return "healthy"
                    if any(s in ("running", "healthy") for s in statuses):
                        return "degraded"
                    return "crashed"
                except Exception:
                    return "unknown"
            return "crashed"

    def _get_topology_service_status(self, service_name: str) -> str:
        """Return the status of a single named service from service_state.json."""
        state_path = os.path.join(self.workspace_dir, "service_state.json")
        if not os.path.isfile(state_path):
            return "unknown"
        try:
            with open(state_path, encoding="utf-8") as f:
                topo = json.load(f)
            svc = topo.get("services", {}).get(service_name, {})
            return svc.get("status", "unknown")
        except Exception:
            return "unknown"

        # For pure code tasks (1-6): use pass rate as proxy
        rate = self._get_test_pass_rate()
        if rate >= 1.0:
            return "healthy"
        if rate > 0.5:
            return "degraded"
        return "crashed"

    def _collect_structured_logs(self) -> Tuple[List[Dict[str, str]], Optional[Dict[str, Any]]]:
        """Collect structured logs and service status from workspace artifacts."""
        logs: List[Dict[str, str]] = []
        service_status: Optional[Dict[str, Any]] = None

        # task_10: topology logs live in per-service subdirectories
        if self.state.task_id == "task_10_pr":
            for svc_name in ("db", "auth", "gateway"):
                svc_log = os.path.join(
                    self.workspace_dir, "services", svc_name, "service.log"
                )
                if os.path.isfile(svc_log):
                    entries = _parse_log_file(svc_log)
                    for e in entries:
                        e["service"] = svc_name
                    logs.extend(entries)
            state_path = os.path.join(self.workspace_dir, "service_state.json")
            if os.path.isfile(state_path):
                try:
                    with open(state_path, encoding="utf-8") as f:
                        service_status = json.load(f)
                except Exception:
                    pass
            return logs[-50:], service_status

        for fname in ("service.log", "migration.log", "app.log"):
            fpath = os.path.join(self.workspace_dir, fname)
            if os.path.isfile(fpath):
                entries = _parse_log_file(fpath)
                logs.extend(entries)

        state_path = os.path.join(self.workspace_dir, "service_state.json")
        if os.path.isfile(state_path):
            try:
                with open(state_path, encoding="utf-8") as f:
                    service_status = json.load(f)
            except Exception:
                pass

        db_path = os.path.join(self.workspace_dir, "app.db")
        if os.path.isfile(db_path) and service_status is None:
            try:
                import sqlite3
                conn = sqlite3.connect(db_path)
                cur = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name NOT LIKE 'sqlite_%'"
                )
                tables = sorted(r[0] for r in cur.fetchall())
                cur2 = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='index' "
                    "AND name NOT LIKE 'sqlite_%'"
                )
                indexes = sorted(r[0] for r in cur2.fetchall())
                conn.close()
                service_status = {
                    "status": "healthy" if len(tables) >= 2 and len(indexes) >= 2 else "degraded",
                    "tables": tables,
                    "indexes": indexes,
                }
            except Exception:
                pass

        return logs[-50:], service_status

    # ─────────────────────────────────────────────────────────────────────────
    # Multi-component terminal reward
    # ─────────────────────────────────────────────────────────────────────────

    def _compute_submit_reward(
        self, pytest_pass_rate: float, root_cause: str
    ) -> Tuple[float, Dict[str, float]]:
        """
        Five-component weighted terminal reward.

        Components (weights sum to 1.0):
          test_quality  0.40  — pytest pass rate [0, 1]
          diagnosis     0.15  — keyword match of root_cause vs task keywords
          efficiency    0.15  — optimal_steps / actual_steps (capped 1.0)
          exploration   0.10  — unique action types used / 5 (capped 1.0)
          trap_avoidance 0.10 — 1.0 - 0.2 * trap_count (floor 0)
          submit_credit  0.10 — 1.0 always (reached submit vs max-steps timeout)

        Gate: if tests don't all pass → cap at 0.49 (partial credit only).
              if all tests pass → floor at 0.50.
        Range: [0.01, 0.99]
        """
        # 1. Test quality
        test_quality = float(pytest_pass_rate)

        # 2. Diagnosis quality — keyword match
        # Supports both flat frozenset (tasks 1-9) and grouped dict (task 10).
        # Grouped dict: {group_name: frozenset(keywords)}
        # Score = fraction of groups with ≥1 keyword hit (more robust than raw overlap).
        keywords = self.current_task_data.get("_diagnosis_keywords", frozenset())
        if keywords and root_cause:
            rc_lower = root_cause.lower()
            if isinstance(keywords, dict):
                groups = list(keywords.values())
                hits = sum(
                    1 for group in groups
                    if any(kw.lower() in rc_lower for kw in group)
                )
                diagnosis = hits / max(len(groups), 1)
            else:
                matched = sum(1 for kw in keywords if kw.lower() in rc_lower)
                diagnosis = min(matched / max(len(keywords), 1), 1.0)
        else:
            diagnosis = 0.0

        # 3. Efficiency — how close to optimal step count
        optimal = self.current_task_data.get("_optimal_steps", 5)
        actual  = max(self.state.step_count, 1)
        if actual <= optimal:
            efficiency = 1.0
        elif actual <= optimal * 2:
            efficiency = optimal / actual
        else:
            efficiency = max(0.1, optimal / actual)

        # 4. Exploration — unique tool types used this episode
        exploration = min(len(self._tool_types_used) / 5.0, 1.0)

        # 5. Trap avoidance — decays per worsening/wasted restart
        trap_avoidance = max(0.0, 1.0 - 0.20 * self.state.trap_count)

        # 6. Submit credit — agent chose to submit (not timed out)
        submit_credit = 1.0

        weights = {
            "test_quality":   0.40,
            "diagnosis":      0.15,
            "efficiency":     0.15,
            "exploration":    0.10,
            "trap_avoidance": 0.10,
            "submit_credit":  0.10,
        }
        components = {
            "test_quality":   test_quality,
            "diagnosis":      diagnosis,
            "efficiency":     efficiency,
            "exploration":    exploration,
            "trap_avoidance": trap_avoidance,
            "submit_credit":  submit_credit,
        }

        raw = sum(weights[k] * components[k] for k in weights)

        # Gate: full pass → floor 0.50; partial → cap 0.49
        if test_quality >= 1.0:
            raw = max(raw, 0.50)
        else:
            raw = min(raw, 0.01 + test_quality * 0.48)

        reward = round(max(0.01, min(0.99, raw)), 4)

        breakdown = {
            k: round(weights[k] * components[k], 4) for k in weights
        }
        breakdown["raw_total"]   = round(raw, 4)
        breakdown["test_quality_score"] = round(test_quality, 4)
        breakdown["diagnosis_score"]    = round(diagnosis, 4)

        return reward, breakdown

    # ─────────────────────────────────────────────────────────────────────────
    # Episode management
    # ─────────────────────────────────────────────────────────────────────────

    def reset(self, task_id: Optional[str] = None) -> CodeObservation:
        self.state.episode_id    = str(uuid.uuid4())
        self.state.step_count    = 0
        self.state.done          = False
        self.state.total_reward  = 0.0
        self.state.system_health = "unknown"
        self.state.progress_depth = 0
        self.state.trap_count    = 0
        self.prev_pass_rate      = 0.0
        self._tool_types_used    = set()
        self._restart_count      = 0
        self._prev_health_at_restart = "unknown"

        task_ids = [t["task_id"] for t in TASKS]

        if task_id is not None and task_id in task_ids:
            self.state.current_task_index = task_ids.index(task_id)
        elif not self._is_first_reset:
            self.state.current_task_index = (
                self.state.current_task_index + 1
            ) % len(TASKS)
        self._is_first_reset = False

        self.current_task_data   = TASKS[self.state.current_task_index]
        self.state.task_id       = self.current_task_data["task_id"]
        self.state.optimal_steps = self.current_task_data.get("_optimal_steps", 5)

        self._setup_workspace()
        available_files = sorted(self._original_repo_files)

        # Detect initial system health for incident tasks
        self.state.system_health = self._detect_system_health()
        logs, service_status = self._collect_structured_logs()

        return CodeObservation(
            task_id=self.state.task_id,
            context=self.current_task_data["description"],
            available_files=available_files,
            action_result="Sandbox ready. Run 'run_tests' to see what is failing.",
            step_number=self.state.step_count,
            done=False,
            reward=0.0,
            logs=logs,
            service_status=service_status,
        )

    def _get_test_pass_rate(self) -> float:
        grader = GRADERS.get(self.state.task_id)
        if grader is None:
            return 0.0
        return grader(self.workspace_dir)

    # ─────────────────────────────────────────────────────────────────────────
    # Step dispatch
    # ─────────────────────────────────────────────────────────────────────────

    def step(
        self, action_obj: AgentAction
    ) -> Tuple[CodeObservation, float, bool, Dict[str, Any]]:

        if self.current_task_data is None:
            obs = CodeObservation(
                task_id="none", context="No active episode.",
                available_files=[], action_result="Call POST /reset before /step.",
                step_number=0, done=False, reward=0.0,
            )
            return obs, 0.0, False, {"error": "no_active_episode_call_reset_first"}

        if self.state.done:
            obs = CodeObservation(
                task_id=self.state.task_id,
                context=self.current_task_data["description"],
                available_files=sorted(self._original_repo_files),
                action_result="Episode is already done. Call /reset to start a new episode.",
                step_number=self.state.step_count, done=True, reward=0.0,
            )
            return obs, 0.0, True, {"error": "episode_already_done"}

        try:
            self.state.step_count += 1
            available_files = sorted(self._original_repo_files)
            obs_result = ""
            reward = 0.0
            done = False
            info: Dict[str, Any] = {}
            env = _make_clean_env(self.workspace_dir)

            # Track tool diversity for exploration score
            self._tool_types_used.add(action_obj.action_type)

            # ── TOOL: execute_command ─────────────────────────────────────────
            if action_obj.action_type == "execute_command":
                cmd = action_obj.command
                if not cmd:
                    obs_result = "Error: No bash command provided."
                    reward = 0.0
                else:
                    try:
                        result = subprocess.run(
                            cmd, cwd=self.workspace_dir, shell=True,
                            capture_output=True, text=True, timeout=15, env=env,
                            start_new_session=True,
                        )
                        output = result.stdout if result.stdout else result.stderr
                        MAX_OUTPUT_CHARS = 4000
                        if len(output) > MAX_OUTPUT_CHARS:
                            output = (
                                f"...[TRUNCATED — {len(output)} chars, "
                                f"showing last {MAX_OUTPUT_CHARS}]...\n"
                                + output[-MAX_OUTPUT_CHARS:]
                            )
                        obs_result = f"--- BASH OUTPUT (Exit {result.returncode}) ---\n{output}"
                        reward = 0.01 if result.returncode == 0 else 0.005
                    except TimeoutExpired:
                        obs_result = "Error: Command timed out after 15 seconds."
                        reward = 0.005
                self._enforce_test_integrity()

            # ── TOOL: read_file ───────────────────────────────────────────────
            elif action_obj.action_type == "read_file":
                path = action_obj.path
                if not path:
                    obs_result = "Error: path is required for read_file."
                    reward = 0.0
                else:
                    workspace_real = os.path.realpath(self.workspace_dir)
                    full_path = os.path.realpath(
                        os.path.join(self.workspace_dir, path)
                    )
                    if not full_path.startswith(workspace_real + os.sep):
                        obs_result = "Security Error: Path traversal detected and blocked."
                        reward = -0.05
                    elif not os.path.isfile(full_path):
                        obs_result = f"Error: File not found: {path}"
                        reward = 0.005
                    else:
                        try:
                            with open(full_path, "r", encoding="utf-8") as f:
                                content = f.read()
                            MAX_CHARS = 4000
                            if len(content) > MAX_CHARS:
                                content = (
                                    content[:MAX_CHARS]
                                    + f"\n...[TRUNCATED — showing first {MAX_CHARS} of "
                                    f"{len(content)} chars]..."
                                )
                            obs_result = f"--- {path} ---\n{content}"
                            reward = 0.01
                        except Exception as e:
                            obs_result = f"Error reading {path}: {e}"
                            reward = 0.005

            # ── TOOL: search_codebase ─────────────────────────────────────────
            elif action_obj.action_type == "search_codebase":
                pattern = action_obj.pattern
                if not pattern:
                    obs_result = "Error: pattern is required for search_codebase."
                    reward = 0.0
                else:
                    try:
                        result = subprocess.run(
                            ["grep", "-rn", "--include=*.py", pattern, "."],
                            cwd=self.workspace_dir,
                            capture_output=True, text=True, timeout=10, env=env,
                        )
                        output = (result.stdout or result.stderr or "").strip()
                        MAX_CHARS = 3000
                        if len(output) > MAX_CHARS:
                            output = output[:MAX_CHARS] + "\n...[TRUNCATED]..."
                        obs_result = (
                            f"--- search: {pattern!r} ---\n{output}"
                            if output else f"No matches for: {pattern!r}"
                        )
                        reward = 0.01
                    except TimeoutExpired:
                        obs_result = "Error: search timed out."
                        reward = 0.005

            # ── TOOL: run_tests ───────────────────────────────────────────────
            elif action_obj.action_type == "run_tests":
                test_path = action_obj.path or "tests/"
                try:
                    result = subprocess.run(
                        f"pytest {test_path} -v --tb=short",
                        cwd=self.workspace_dir, shell=True,
                        capture_output=True, text=True, timeout=20, env=env,
                        start_new_session=True,
                    )
                    output = result.stdout + result.stderr
                    MAX_OUTPUT_CHARS = 4000
                    if len(output) > MAX_OUTPUT_CHARS:
                        output = (
                            f"...[TRUNCATED — showing last {MAX_OUTPUT_CHARS}]...\n"
                            + output[-MAX_OUTPUT_CHARS:]
                        )
                    obs_result = f"--- pytest {test_path} (Exit {result.returncode}) ---\n{output}"
                    reward = 0.01 if result.returncode == 0 else 0.005
                except TimeoutExpired:
                    obs_result = "Error: pytest timed out after 20 seconds."
                    reward = 0.005
                self._enforce_test_integrity()

            # ── TOOL: inspect_logs ────────────────────────────────────────────
            elif action_obj.action_type == "inspect_logs":
                # task_10: per-service log files; optional service_name filter
                if self.state.task_id == "task_10_pr":
                    svc_filter = getattr(action_obj, "service_name", None)
                    svc_names = (
                        [svc_filter]
                        if svc_filter and svc_filter in ("db", "auth", "gateway")
                        else ["db", "auth", "gateway"]
                    )
                    found = []
                    for svc in svc_names:
                        svc_log = os.path.join(
                            self.workspace_dir, "services", svc, "service.log"
                        )
                        if os.path.isfile(svc_log):
                            found.append((f"services/{svc}/service.log", svc_log))
                    # also include root-level service.log if present
                    root_log = os.path.join(self.workspace_dir, "service.log")
                    if os.path.isfile(root_log):
                        found.append(("service.log", root_log))
                else:
                    log_names = ["service.log", "migration.log", "app.log"]
                    found = []
                    for fname in log_names:
                        fpath = os.path.join(self.workspace_dir, fname)
                        if os.path.isfile(fpath):
                            found.append((fname, fpath))

                if not found:
                    obs_result = (
                        "No log files found yet. Logs are generated after service operations. "
                        "Call restart_service to run app_init.py and produce logs."
                    )
                    reward = 0.005
                else:
                    parts = []
                    for fname, fpath in found:
                        try:
                            with open(fpath, "r", encoding="utf-8") as f:
                                content = f.read()
                            if len(content) > 2000:
                                content = "...[showing last 2000 chars]...\n" + content[-2000:]
                            parts.append(f"--- {fname} ---\n{content}")
                        except Exception as e:
                            parts.append(f"--- {fname} ---\nError reading: {e}")
                    obs_result = "\n\n".join(parts)
                    reward = 0.01

            # ── TOOL: patch_file ──────────────────────────────────────────────
            elif action_obj.action_type == "patch_file":
                target = action_obj.target_file
                new_content = action_obj.new_content
                if not target or not new_content:
                    obs_result = "Error: target_file and new_content are required."
                    reward = 0.0
                else:
                    workspace_real = os.path.realpath(self.workspace_dir)
                    full_path = os.path.realpath(
                        os.path.join(self.workspace_dir, target)
                    )
                    tests_real = os.path.realpath(
                        os.path.join(self.workspace_dir, "tests")
                    )

                    if not full_path.startswith(workspace_real + os.sep):
                        obs_result = "Security Error: Path traversal detected and blocked."
                        reward = -0.05
                        self.state.trap_count += 1

                    elif full_path.startswith(tests_real + os.sep) or full_path == tests_real:
                        obs_result = "Security Error: Test files are read-only and cannot be modified."
                        reward = -0.05
                        self.state.trap_count += 1

                    else:
                        _ctrl_rel = os.path.relpath(
                            full_path, workspace_real
                        ).replace(os.sep, "/")
                        if (os.path.basename(full_path) in _PYTEST_CONTROL_NAMES
                                and _ctrl_rel not in self._original_repo_files):
                            obs_result = (
                                "Security Error: Creating pytest configuration files "
                                "(conftest.py, pytest.ini, pyproject.toml, etc.) is not allowed."
                            )
                            reward = -0.05
                            self.state.trap_count += 1
                            full_path = None

                        if full_path is not None:
                            os.makedirs(os.path.dirname(full_path), exist_ok=True)
                        import py_compile, tempfile
                        if full_path is not None and full_path.endswith(".py"):
                            try:
                                with tempfile.NamedTemporaryFile(
                                    mode="w", suffix=".py", delete=False, encoding="utf-8"
                                ) as tmp:
                                    tmp.write(new_content)
                                    tmp_path = tmp.name
                                py_compile.compile(tmp_path, doraise=True)
                                os.unlink(tmp_path)
                            except py_compile.PyCompileError as e:
                                os.unlink(tmp_path)
                                obs_result = (
                                    f"Syntax Error in patch: {e}. "
                                    "File was NOT written. Fix the syntax and try again."
                                )
                                reward = 0.005
                                full_path = None

                        if full_path is not None and not obs_result:
                            with open(full_path, "w", encoding="utf-8") as f:
                                f.write(new_content)
                            obs_result = f"Successfully patched: {target}"

                            raw = self._get_test_pass_rate()
                            delta = round(raw - self.prev_pass_rate, 4)

                            if delta > 0:
                                reward = round(0.01 + delta * 0.88, 4)
                                self.prev_pass_rate = raw
                            elif delta < 0:
                                # Regression: negative reward, floored at -0.1
                                reward = round(max(-0.1, 0.01 + delta * 0.88), 4)
                            else:
                                # No change: small positive to reward the attempt
                                reward = 0.005

                            info["test_pass_rate"] = round(raw, 4)
                            self.state.total_reward = round(
                                max(self.state.total_reward, reward), 4
                            )

            # ── TOOL: restart_service ─────────────────────────────────────────
            elif action_obj.action_type == "restart_service":
                app_init_path = os.path.join(self.workspace_dir, "app_init.py")
                if not os.path.isfile(app_init_path):
                    obs_result = (
                        "Error: app_init.py not found in workspace root. "
                        "This action only applies to service/database tasks (7-10)."
                    )
                    reward = 0.005
                else:
                    prev_health = self._detect_system_health()

                    # task_10: support targeted --service flag
                    svc_arg = getattr(action_obj, "service_name", None)
                    if (self.state.task_id == "task_10_pr"
                            and svc_arg
                            and svc_arg in ("db", "auth", "gateway")):
                        run_cmd = f"python app_init.py --service {svc_arg}"
                    else:
                        run_cmd = "python app_init.py"

                    # For task_10 targeted restarts, snapshot the specific
                    # service status before running so we can detect per-service
                    # progress even when global health stays "degraded".
                    prev_svc_status = None
                    if self.state.task_id == "task_10_pr" and svc_arg:
                        prev_svc_status = self._get_topology_service_status(svc_arg)

                    try:
                        result = subprocess.run(
                            run_cmd,
                            cwd=self.workspace_dir, shell=True,
                            capture_output=True, text=True, timeout=15, env=env,
                            start_new_session=True,
                        )
                        output = (result.stdout + result.stderr).strip()
                        MAX_CHARS = 2000
                        if len(output) > MAX_CHARS:
                            output = output[-MAX_CHARS:]

                        new_health = self._detect_system_health()
                        self.state.system_health = new_health

                        # Emit structured log entry for this restart attempt
                        ts_now = __import__("datetime").datetime.utcnow().isoformat() + "Z"
                        info["restart_outcome"] = new_health
                        info["prev_health"]     = prev_health

                        # ── task_10 per-service scoring ───────────────────────
                        # When a specific service is targeted, compare its status
                        # directly. Global health stays "degraded" until ALL
                        # services recover — using it alone would misclassify a
                        # correct intermediate restart as no_effect.
                        if prev_svc_status is not None:
                            new_svc_status = self._get_topology_service_status(svc_arg)
                            _good = ("running", "healthy")
                            if new_svc_status in _good and prev_svc_status not in _good:
                                # This service recovered — genuine progress
                                self.state.progress_depth += 1
                                reward = 0.04
                                outcome_label = "partial_progress"
                            elif new_svc_status == "degraded" and prev_svc_status in ("crashed", "unknown"):
                                # Moved from crashed → degraded — still progress
                                self.state.progress_depth += 1
                                reward = 0.02
                                outcome_label = "partial_progress"
                            elif new_svc_status in _good and prev_svc_status in _good:
                                # Service was already healthy — wasted restart
                                self.state.trap_count += 1
                                reward = 0.005
                                outcome_label = "no_effect"
                            elif new_svc_status == prev_svc_status:
                                # No change for this service — fix is incomplete
                                self.state.trap_count += 1
                                reward = 0.005
                                outcome_label = "no_effect"
                            else:
                                reward = 0.01
                                outcome_label = "progress"
                            # If all services are now healthy, upgrade to full progress
                            # (no extra progress_depth increment — already counted above)
                            if new_health in ("running", "healthy"):
                                reward = 0.05
                                outcome_label = "progress"
                            info["svc_health"] = f"{prev_svc_status}→{new_svc_status}"

                        # ── global health scoring (tasks 7-9) ─────────────────
                        elif new_health in ("running", "healthy") and prev_health not in ("running", "healthy"):
                            # Full recovery: system is healthy
                            self.state.progress_depth += 1
                            reward = 0.05
                            outcome_label = "progress"
                        elif new_health == "degraded" and prev_health in ("oom_killed", "crashed", "unknown"):
                            # Partial fix: one component recovered, another still broken.
                            self.state.progress_depth += 1
                            reward = 0.03
                            outcome_label = "partial_progress"
                        elif new_health == prev_health:
                            # No change — restart was premature or incomplete fix
                            self.state.trap_count += 1
                            reward = 0.005
                            outcome_label = "no_effect"
                        elif new_health in ("crashed", "oom_killed"):
                            # Still fully broken or worsened
                            self.state.trap_count += 1
                            reward = 0.005
                            outcome_label = "worsened"
                        else:
                            reward = 0.01
                            outcome_label = "progress" if result.returncode == 0 else "no_effect"

                        info["outcome"] = outcome_label
                        obs_result = (
                            f"[restart_service] outcome={outcome_label} "
                            f"health={prev_health}→{new_health} "
                            f"(exit {result.returncode}):\n{output}"
                        )

                    except TimeoutExpired:
                        obs_result = "Error: app_init.py timed out after 15 seconds."
                        reward = 0.005

                    self._restart_count += 1
                self._enforce_test_integrity()

            # ── TOOL: submit_fix (primary terminal) ───────────────────────────
            elif action_obj.action_type in ("submit_fix", "submit_review"):
                root_cause = action_obj.root_cause or action_obj.summary or ""
                try:
                    result = subprocess.run(
                        "pytest tests/ -v",
                        cwd=self.workspace_dir, shell=True,
                        capture_output=True, text=True, timeout=15, env=env,
                    )
                    if result.returncode == 0:
                        raw_pass_rate = 1.0
                        pytest_feedback = "SUCCESS: All tests passed."
                    else:
                        raw_pass_rate = self._get_test_pass_rate()
                        pytest_feedback = f"FAILED: Tests still failing.\n{result.stdout[-500:]}"
                    info["test_pass_rate"] = round(raw_pass_rate, 4)
                except TimeoutExpired:
                    raw_pass_rate = 0.0
                    pytest_feedback = "FAILED: Test suite timed out."
                    info["test_pass_rate"] = 0.0

                reward, breakdown = self._compute_submit_reward(raw_pass_rate, root_cause)
                obs_result = f"Evaluation complete. {pytest_feedback}"
                done = True
                self.state.total_reward = max(self.state.total_reward, reward)
                info["feedback"]   = pytest_feedback
                info["root_cause"] = root_cause
                info["reward_breakdown"] = breakdown
                shutil.rmtree(self.workspace_dir, ignore_errors=True)

            # ── Max steps cutoff ──────────────────────────────────────────────
            if self.state.step_count >= MAX_STEPS and not done:
                done = True
                obs_result += f"\n[System] Max steps ({MAX_STEPS}) reached — episode terminated."
                shutil.rmtree(self.workspace_dir, ignore_errors=True)

            self.state.done = done
            logs, service_status = self._collect_structured_logs() if not done else ([], None)

            obs = CodeObservation(
                task_id=self.state.task_id,
                context=self.current_task_data["description"],
                available_files=available_files,
                action_result=obs_result,
                step_number=self.state.step_count,
                done=done,
                reward=reward,
                logs=logs,
                service_status=service_status,
            )
            return obs, reward, done, info

        except Exception as e:
            current_done = self.state.done
            obs = CodeObservation(
                task_id=self.state.task_id if self.current_task_data else "error",
                context="Error state",
                available_files=[],
                action_result=f"Internal Error: {str(e)}",
                step_number=self.state.step_count,
                done=current_done,
                reward=0.0,
            )
            return obs, 0.0, current_done, {"error": str(e)}
