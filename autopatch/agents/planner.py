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
  "bug_description" – what is wrong (use technical terms: function names, variable names, error types)
  "fix_strategy"    – EXACT fix: copy the fix hint from code comments if present, otherwise be specific
  "bug_type"        – one of: sql_injection, weak_crypto, logic_error, hardcoded_secret, async_error

CRITICAL RULES FOR fix_strategy:
- If the file has inline comments like "Fix: use X", copy that EXACT fix into fix_strategy
- For database migrations: use conn.executescript(sql) NOT conn.executemany() for multi-statement SQL
- For sort order bugs: use key=lambda f: int(f.split('_')[0]) for numeric file ordering
- For async bugs: use 'async def' AND 'await asyncio.sleep()' NOT 'await asyncio.wait()'
- bug_type must match the actual bug: sorting/logic bugs = logic_error; NOT sql_injection

Ordering rule: bugs that block more failing tests appear first in the array.
Service dependency ordering: when the task describes a gateway → auth → db cascade, ALWAYS output tasks in dependency order: db first, then auth, then gateway. Never restart gateway before db/auth are fixed — it will worsen the cascade.

Example:
[{"file": "db/migrator.py", "bug_description": "sorted() uses lexicographic order, bare pass swallows exceptions, conn.execute drops multi-statement SQL", "fix_strategy": "sort key=lambda f: int(f.split('_')[0]), reraise exceptions, use conn.executescript(sql)", "bug_type": "logic_error"}]

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

        # Merge multiple tasks for the same file into one comprehensive task
        merged: dict = {}  # file → merged task
        for task in tasks:
            if not isinstance(task, dict):
                continue
            if task.get("file") not in available_files:
                logger.warning("[PLANNER] Dropping task for unknown file: %s", task.get("file"))
                continue
            # Skip test files, __init__.py, and non-Python files — never patch these
            file_name = task.get("file", "")
            if (file_name.endswith("__init__.py")
                    or not file_name.endswith(".py")
                    or "/tests/" in file_name
                    or file_name.startswith("tests/")):
                logger.info("[PLANNER] Skipping non-patch-target: %s", file_name)
                continue
            # Normalise unknown bug_type
            if task.get("bug_type") not in VALID_BUG_TYPES:
                task["bug_type"] = "logic_error"

            file_key = task.get("file", "")
            if file_key not in merged:
                merged[file_key] = task.copy()
            else:
                # Merge: combine bug descriptions and fix strategies
                existing = merged[file_key]
                existing["bug_description"] = (
                    existing.get("bug_description", "") + "; " + task.get("bug_description", "")
                )
                existing["fix_strategy"] = (
                    existing.get("fix_strategy", "") + "; " + task.get("fix_strategy", "")
                )
                # Use the more specific bug_type (not logic_error if possible)
                if existing.get("bug_type") == "logic_error" and task.get("bug_type") != "logic_error":
                    existing["bug_type"] = task.get("bug_type")
                logger.info("[PLANNER] Merged additional task into file: %s", file_key)

        result = list(merged.values())[:3]  # cap at 3 files

        logger.info("[PLANNER] Produced %d task(s): %s", len(result), [t.get("file") for t in result])
        return result

    except json.JSONDecodeError as exc:
        logger.error("[PLANNER] JSON parse error: %s | raw: %.300s", exc, raw)
        return []
    except Exception as exc:
        logger.error("[PLANNER] Unexpected error: %s", exc)
        return []
