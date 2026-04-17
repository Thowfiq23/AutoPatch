"""
autopatch/agents/memory.py
---------------------------
Persistent storage layer for fix patterns, episode trajectories, and run logs.

Enables the Evolver to analyse historical performance and the Coder to reuse
successful past fixes. Works with or without Redis — falls back to a local
JSON file transparently.

Redis detection happens at import time. If Redis is unreachable, USE_REDIS is
set to False and all operations use the JSON fallback silently.
"""

import hashlib
import json
import logging
import os
import tempfile
import time
from typing import List

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Redis detection (at import time)
# ---------------------------------------------------------------------------

_REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
_JSON_FALLBACK_PATH = os.path.join(
    os.path.dirname(__file__), "..", "episodes.json"
)
_JSON_FALLBACK_PATH = os.path.normpath(_JSON_FALLBACK_PATH)

USE_REDIS = False
_redis_client = None

try:
    import redis as _redis_lib
    _redis_client = _redis_lib.from_url(_REDIS_URL, decode_responses=True)
    _redis_client.ping()
    USE_REDIS = True
    logger.info("[MEMORY] Redis connected at %s", _REDIS_URL)
except Exception as _exc:
    USE_REDIS = False
    _redis_client = None
    logger.info("[MEMORY] Redis unavailable (%s). Using JSON fallback: %s", _exc, _JSON_FALLBACK_PATH)

_REDIS_LIST_CAP = 50  # max patterns per bug_type in Redis


# ---------------------------------------------------------------------------
# JSON fallback helpers
# ---------------------------------------------------------------------------

def _load_json() -> dict:
    """Load the episodes.json file, returning empty structure on any error."""
    try:
        with open(_JSON_FALLBACK_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"patterns": {}, "trajectories": {}, "logs": {}}


def _save_json(data: dict) -> None:
    """Atomically write data to episodes.json (write-to-tmp then rename)."""
    os.makedirs(os.path.dirname(_JSON_FALLBACK_PATH), exist_ok=True)
    dir_path = os.path.dirname(_JSON_FALLBACK_PATH)
    try:
        with tempfile.NamedTemporaryFile(
            "w", dir=dir_path, delete=False, suffix=".tmp", encoding="utf-8"
        ) as tmp:
            json.dump(data, tmp, indent=2)
            tmp_path = tmp.name
        os.replace(tmp_path, _JSON_FALLBACK_PATH)
    except Exception as exc:
        logger.error("[MEMORY] Failed to write JSON fallback: %s", exc)


# ---------------------------------------------------------------------------
# Pattern storage
# ---------------------------------------------------------------------------

_MIN_PATTERN_REWARD = 0.5  # only store patterns that meaningfully improved the score


def store_pattern(bug_type: str, patch_content: str, reward: float) -> bool:
    """
    Store a patch as a reusable fix pattern. Returns True if stored, False if skipped.

    Key format (Redis): pattern:{bug_type}:{md5_hash[:8]}
    Stored value: JSON with patch_content, reward, timestamp.
    Redis: LPUSH to a list per bug_type, capped at 50 entries via LTRIM.
    JSON: appended to patterns[bug_type] list.

    Never raises. Silently ignores low-reward patches (reward < 0.5).
    """
    if reward < _MIN_PATTERN_REWARD:
        logger.debug("[MEMORY] Skipping low-reward pattern (%.3f < %.1f) for %s", reward, _MIN_PATTERN_REWARD, bug_type)
        return False
    try:
        content_hash = hashlib.md5(patch_content.encode("utf-8")).hexdigest()[:8]
        redis_key = f"pattern:{bug_type}:{content_hash}"
        record = {
            "key": redis_key,
            "patch_content": patch_content,
            "reward": reward,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }

        if USE_REDIS and _redis_client:
            list_key = f"patterns:{bug_type}"
            _redis_client.lpush(list_key, json.dumps(record))
            _redis_client.ltrim(list_key, 0, _REDIS_LIST_CAP - 1)
            logger.debug("[MEMORY] Stored Redis pattern %s reward=%.3f", redis_key, reward)
        else:
            data = _load_json()
            patterns = data.setdefault("patterns", {})
            bug_list = patterns.setdefault(bug_type, [])
            bug_list.insert(0, record)        # newest first
            patterns[bug_type] = bug_list[:_REDIS_LIST_CAP]
            _save_json(data)
            logger.debug("[MEMORY] Stored JSON pattern %s reward=%.3f", redis_key, reward)
        return True

    except Exception as exc:
        logger.error("[MEMORY] store_pattern error: %s", exc)
        return False


def retrieve_pattern(bug_type: str) -> str:
    """
    Return the best past patch for bug_type where reward >= 0.9.

    Returns empty string (never None, never raises) when no match is found.
    Truncated to 800 characters.
    """
    try:
        if USE_REDIS and _redis_client:
            list_key = f"patterns:{bug_type}"
            items = _redis_client.lrange(list_key, 0, _REDIS_LIST_CAP - 1)
            best = ""
            best_reward = 0.0
            for item_str in items:
                try:
                    rec = json.loads(item_str)
                    if rec.get("reward", 0.0) >= 0.9 and rec.get("reward", 0.0) > best_reward:
                        best_reward = rec["reward"]
                        best = rec.get("patch_content", "")
                except Exception:
                    continue
            return best[:800] if best else ""

        else:
            data = _load_json()
            patterns = data.get("patterns", {}).get(bug_type, [])
            best = ""
            best_reward = 0.0
            for rec in patterns:
                if rec.get("reward", 0.0) >= 0.9 and rec.get("reward", 0.0) > best_reward:
                    best_reward = rec["reward"]
                    best = rec.get("patch_content", "")
            return best[:800] if best else ""

    except Exception as exc:
        logger.error("[MEMORY] retrieve_pattern error: %s", exc)
        return ""


# ---------------------------------------------------------------------------
# Trajectory storage
# ---------------------------------------------------------------------------

def store_trajectory(run_id: str, episode: int, rewards: List[float], score: float) -> None:
    """
    Store a full episode trajectory for Evolver analysis.

    Never raises.
    """
    try:
        record = {
            "run_id": run_id,
            "episode": episode,
            "rewards": rewards,
            "score": score,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }

        if USE_REDIS and _redis_client:
            list_key = f"trajectories:{run_id}"
            _redis_client.rpush(list_key, json.dumps(record))
            logger.debug("[MEMORY] Stored trajectory run_id=%s ep=%d score=%.3f", run_id, episode, score)
        else:
            data = _load_json()
            traj = data.setdefault("trajectories", {})
            run_list = traj.setdefault(run_id, [])
            run_list.append(record)
            _save_json(data)
            logger.debug("[MEMORY] Stored JSON trajectory run_id=%s ep=%d score=%.3f", run_id, episode, score)

    except Exception as exc:
        logger.error("[MEMORY] store_trajectory error: %s", exc)


def get_trajectories(run_id: str) -> List[dict]:
    """
    Return all stored trajectories for a run_id, in order.

    Returns empty list on any error.
    """
    try:
        if USE_REDIS and _redis_client:
            list_key = f"trajectories:{run_id}"
            items = _redis_client.lrange(list_key, 0, -1)
            result = []
            for item_str in items:
                try:
                    result.append(json.loads(item_str))
                except Exception:
                    continue
            return result
        else:
            data = _load_json()
            return data.get("trajectories", {}).get(run_id, [])

    except Exception as exc:
        logger.error("[MEMORY] get_trajectories error: %s", exc)
        return []


# ---------------------------------------------------------------------------
# Log storage (used by SSE endpoint in server.py)
# ---------------------------------------------------------------------------

def store_log(run_id: str, line: str) -> None:
    """
    Append a single log line for a run_id. Used by the SSE /logs endpoint.

    Never raises.
    """
    try:
        if USE_REDIS and _redis_client:
            list_key = f"logs:{run_id}"
            _redis_client.rpush(list_key, line)
        else:
            data = _load_json()
            logs = data.setdefault("logs", {})
            run_logs = logs.setdefault(run_id, [])
            run_logs.append(line)
            _save_json(data)
    except Exception as exc:
        logger.error("[MEMORY] store_log error: %s", exc)


def get_logs(run_id: str) -> List[str]:
    """
    Return all stored log lines for a run_id in insertion order.

    Returns empty list on any error.
    """
    try:
        if USE_REDIS and _redis_client:
            list_key = f"logs:{run_id}"
            return _redis_client.lrange(list_key, 0, -1) or []
        else:
            data = _load_json()
            return data.get("logs", {}).get(run_id, [])
    except Exception as exc:
        logger.error("[MEMORY] get_logs error: %s", exc)
        return []
