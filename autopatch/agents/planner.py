"""
autopatch/agents/planner.py
----------------------------
Planner agent: reads pytest failure output + PR context and produces a
structured list of fix tasks for the Coder to execute.

First node in the LangGraph pipeline. Determines what to fix and in what
order before any code is written.
"""

import os
import json
import logging

from langchain_groq import ChatGroq
from langchain_core.messages import SystemMessage, HumanMessage

logger = logging.getLogger(__name__)

VALID_BUG_TYPES = {"sql_injection", "weak_crypto", "logic_error", "hardcoded_secret", "async_error"}

_MODEL = os.getenv("MODEL_NAME", "llama-3.3-70b-versatile")

SYSTEM_PROMPT = """You are a code review planner. Analyze pytest failure output and PR context to identify bugs.

Return ONLY a valid JSON array — no markdown fences, no preamble, no explanation.

Each element must be a JSON object with exactly these keys:
  "file"            – relative path of the file to fix (must be from available_files)
  "bug_description" – what is wrong
  "fix_strategy"    – how to fix it
  "bug_type"        – one of: sql_injection, weak_crypto, logic_error, hardcoded_secret, async_error

Ordering rule: bugs that block more failing tests appear first in the array.

Example output:
[{"file": "auth/crypto.py", "bug_description": "Uses MD5 for password hashing", "fix_strategy": "Replace hashlib.md5 with hashlib.pbkdf2_hmac('sha256', ...)", "bug_type": "weak_crypto"}]

If no bugs are found, return an empty array: []
Output ONLY the JSON array — nothing else."""


def plan(obs: dict) -> list:
    """
    Accept a CodeObservation dict and return a list of fix task dicts.

    Each task: {file, bug_description, fix_strategy, bug_type}

    Never raises — returns [] on any failure.
    """
    context = obs.get("context", "")
    action_result = obs.get("action_result", "")
    available_files = obs.get("available_files", [])

    user_content = (
        f"PR / Issue Context:\n{context}\n\n"
        f"Pytest Output:\n{action_result}\n\n"
        f"Available files in sandbox:\n{json.dumps(available_files)}\n\n"
        "Identify all bugs that need fixing. Only reference files listed in available_files."
    )

    raw = ""
    try:
        llm = ChatGroq(model=_MODEL, temperature=0.0)
        messages = [SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=user_content)]
        response = llm.invoke(messages)
        raw = (response.content or "").strip()

        # Strip optional ```json fences
        if raw.startswith("```json"):
            raw = raw[7:]
        elif raw.startswith("```"):
            raw = raw[3:]
        if raw.endswith("```"):
            raw = raw[:-3]
        raw = raw.strip()

        tasks = json.loads(raw)

        if not isinstance(tasks, list):
            logger.error("[PLANNER] LLM returned non-list: %.200s", raw)
            return []

        result = []
        for task in tasks:
            if not isinstance(task, dict):
                continue
            # Filter files not present in the sandbox
            if task.get("file") not in available_files:
                logger.warning("[PLANNER] Dropping task for unknown file: %s", task.get("file"))
                continue
            # Normalise unknown bug_type
            if task.get("bug_type") not in VALID_BUG_TYPES:
                task["bug_type"] = "logic_error"
            result.append(task)

        logger.info("[PLANNER] Produced %d task(s): %s", len(result), [t.get("file") for t in result])
        return result

    except json.JSONDecodeError as exc:
        logger.error("[PLANNER] JSON parse error: %s | raw: %.300s", exc, raw)
        return []
    except Exception as exc:
        logger.error("[PLANNER] Unexpected error: %s", exc)
        return []
