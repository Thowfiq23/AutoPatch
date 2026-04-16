"""
autopatch/agents/coder.py
--------------------------
Coder agent: takes a single fix task and generates a complete corrected
file using an LLM.

Supports dynamic prompt evolution (set_system / get_system / reset_system)
so the Evolver agent can rewrite the system prompt between episodes.
"""

import os
import json
import logging

from langchain_groq import ChatGroq
from langchain_core.messages import SystemMessage, HumanMessage

logger = logging.getLogger(__name__)

_MODEL = os.getenv("MODEL_NAME", "llama-3.3-70b-versatile")

# ---------------------------------------------------------------------------
# Mutable system prompt — Evolver calls set_system() to improve it over time
# ---------------------------------------------------------------------------

BASE_SYSTEM = """You are an expert Python security engineer. Fix the described bug and return the COMPLETE corrected file.

Return ONLY valid JSON (no markdown fences, no preamble) with this exact structure:
{"target_file": "path/to/file.py", "new_content": "...complete corrected Python file...", "explanation": "one-line description of what was changed"}

MANDATORY RULES BY BUG TYPE — follow these exactly or the patch will be rejected:

sql_injection:
  - NEVER use f-strings or .format() to build SQL queries.
  - ALWAYS use parameterized queries: cursor.execute("SELECT ... WHERE col = %s", (value,))
  - The second argument to execute() must be a tuple: (value,) not just value.

weak_crypto:
  - NEVER use hashlib.md5() or hashlib.sha1() for password hashing or security-sensitive operations.
  - ALWAYS use hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, 100000).
  - Never use bcrypt or argon2 unless they are already imported — prefer pbkdf2_hmac.

hardcoded_secret:
  - NEVER hardcode API keys, tokens, or secrets as string literals.
  - ALWAYS replace them with os.getenv('VARIABLE_NAME') and add import os if missing.

async_error:
  - Functions that call async operations MUST be declared with: async def function_name(...)
  - All awaitable calls inside them MUST use: await some_coroutine()
  - Both 'async def' AND 'await' must be present.

logic_error:
  - Fix the specific logic bug described in the task. Do not change unrelated code.

IMPORTANT:
  - Return the COMPLETE file content — every line, not just the diff.
  - new_content must be valid Python (compilable with py_compile).
  - Do not add unnecessary imports, comments, or docstrings.
  - Preserve all existing functionality that is not part of the bug."""

_current_system: str = BASE_SYSTEM


def set_system(prompt: str) -> None:
    """Replace the active system prompt. Called by the Evolver agent."""
    global _current_system
    _current_system = prompt


def get_system() -> str:
    """Return the currently active system prompt."""
    return _current_system


def reset_system() -> None:
    """Restore the original BASE_SYSTEM prompt."""
    global _current_system
    _current_system = BASE_SYSTEM


# ---------------------------------------------------------------------------
# Core code() function
# ---------------------------------------------------------------------------

def code(file_content: str, task: dict, memory_hint: str = "") -> dict:
    """
    Generate a corrected file for a single fix task.

    Parameters
    ----------
    file_content : str
        Current content of the file to patch.
    task : dict
        A task dict from the Planner: {file, bug_description, fix_strategy, bug_type}.
    memory_hint : str, optional
        A successful past fix for the same bug type (from the Memory agent).
        Injected into the user message, truncated to 800 chars.

    Returns
    -------
    dict
        {target_file: str, new_content: str, explanation: str}

    Raises
    ------
    ValueError
        If the LLM response cannot be parsed into the required structure.
    """
    user_content = (
        f"Fix the following bug:\n\n"
        f"File: {task.get('file')}\n"
        f"Bug type: {task.get('bug_type')}\n"
        f"Bug description: {task.get('bug_description')}\n"
        f"Fix strategy: {task.get('fix_strategy')}\n\n"
        f"Current file content:\n{file_content}"
    )

    if memory_hint:
        user_content += f"\n\nSimilar past fix that worked: {memory_hint[:800]}"

    llm = ChatGroq(model=_MODEL, temperature=0.0)
    messages = [SystemMessage(content=_current_system), HumanMessage(content=user_content)]
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

    result = json.loads(raw)

    if not isinstance(result, dict) or "target_file" not in result or "new_content" not in result:
        raise ValueError(
            f"Coder output missing required keys (target_file, new_content). Got: {str(result)[:300]}"
        )

    return result


def code_with_retry(file_content: str, task: dict, memory_hint: str = "",
                    critic_feedback: str = "") -> dict:
    """
    Like code(), but appends critic feedback as a second user message so the
    LLM incorporates the specific rejection reason on retry.

    Used by the orchestrator when approved=False from the Critic.
    """
    user_content = (
        f"Fix the following bug:\n\n"
        f"File: {task.get('file')}\n"
        f"Bug type: {task.get('bug_type')}\n"
        f"Bug description: {task.get('bug_description')}\n"
        f"Fix strategy: {task.get('fix_strategy')}\n\n"
        f"Current file content:\n{file_content}"
    )

    if memory_hint:
        user_content += f"\n\nSimilar past fix that worked: {memory_hint[:800]}"

    from langchain_core.messages import AIMessage
    llm = ChatGroq(model=_MODEL, temperature=0.0)

    messages = [SystemMessage(content=_current_system), HumanMessage(content=user_content)]

    if critic_feedback:
        # Simulate a first attempt that was rejected, then add the feedback
        messages.append(AIMessage(content="{}"))  # placeholder prior attempt
        messages.append(HumanMessage(
            content=(
                f"Your previous patch was rejected by the code reviewer. "
                f"Reason: {critic_feedback}\n\n"
                "Fix these issues and return the corrected JSON patch."
            )
        ))

    response = llm.invoke(messages)
    raw = (response.content or "").strip()

    if raw.startswith("```json"):
        raw = raw[7:]
    elif raw.startswith("```"):
        raw = raw[3:]
    if raw.endswith("```"):
        raw = raw[:-3]
    raw = raw.strip()

    result = json.loads(raw)

    if not isinstance(result, dict) or "target_file" not in result or "new_content" not in result:
        raise ValueError(
            f"Coder retry output missing required keys. Got: {str(result)[:300]}"
        )

    return result
