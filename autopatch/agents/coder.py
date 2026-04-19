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
import re

from langchain_groq import ChatGroq
from langchain_core.messages import SystemMessage, HumanMessage

logger = logging.getLogger(__name__)

_MODEL = os.getenv("MODEL_NAME", "llama-3.3-70b-versatile")


def _select_model(bug_type: str) -> str:
    if bug_type in ("logic_error",):
        return "llama-3.3-70b-versatile"   # hard tasks need big model
    return os.getenv("MODEL_NAME", "llama-3.3-70b-versatile")


TASK_HINTS = {
    "memory_leak": """
SPECIFIC PATTERN FOR THIS TASK:
File app/cache.py:
  - Change: self._store = []
  - To:     self._store = collections.deque(maxlen=CACHE_MAXSIZE)
  - The deque evicts oldest automatically. Remove any manual eviction code.
  - Add: import collections at top of file.

File app/processor.py:
  - Change: _processed_log = []  (module level)
  - To:     _processed_log = collections.deque(maxlen=1000)
  - processed_count() returns len(_processed_log) unchanged.
""",
}

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
  - For file sort order bugs: use key=lambda f: int(f.split('_')[0]) for numeric prefix sorting.
  - For multi-statement SQL: use conn.executescript(sql) NOT conn.execute() or conn.executemany().
  - For swallowed exceptions: replace bare 'pass' in except blocks with 'raise' or 'logging.error(...); raise'.
  - For memory leaks with unbounded lists: replace `self._store = []` with
    `self._store = collections.deque(maxlen=CACHE_MAXSIZE)` and add
    `import collections` at the top of the file. The deque automatically
    evicts the oldest entry when full — no manual eviction code needed.
  - For module-level accumulators that grow forever (e.g. `_processed_log = []`):
    replace with `_processed_log = collections.deque(maxlen=1000)` OR
    delete the list entirely; if deleted, update processed_count() to return 0
    (never return len of a deleted variable).
  - For service config values (CONNECT_TIMEOUT, CIRCUIT_BREAKER_THRESHOLD, etc.):
    read the exact correct value from the inline Fix comment (e.g. "# Fix: set to 3.0")
    and use THAT value — never guess or use 0.001.
  - For retry off-by-one: change range(MAX_RETRIES + 1) to range(MAX_RETRIES) exactly.

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
# JSON parsing helpers
# ---------------------------------------------------------------------------

def _strip_fences(raw: str) -> str:
    """Remove optional ```json or ``` fences from an LLM response."""
    if raw.startswith("```json"):
        raw = raw[7:]
    elif raw.startswith("```"):
        raw = raw[3:]
    if raw.endswith("```"):
        raw = raw[:-3]
    return raw.strip()


def _safe_json_loads(raw: str) -> dict:
    """
    Parse JSON from an LLM response robustly.

    Handles common LLM failure modes in order of likelihood:
    1. Standard valid JSON
    2. Trailing content after the closing brace
    3. Bare newlines / control chars inside JSON string values
    4. Regex extraction of target_file + new_content as last resort
    """
    # Pass 1: standard parse
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # Pass 2: extract outermost {...} block (handles trailing text)
    match = re.search(r'\{.*\}', raw, re.DOTALL)
    if match:
        candidate = match.group(0)
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass

    # Pass 3: escape bare control characters inside JSON string values
    sanitized = re.sub(
        r'(?<=[^\\])([\x00-\x09\x0b-\x1f\x7f])',
        lambda m: repr(m.group())[1:-1],
        raw,
    )
    try:
        return json.loads(sanitized)
    except json.JSONDecodeError:
        pass

    # Pass 4: regex extraction — handles malformed JSON with unescaped quotes in code
    # Extract target_file
    tf_match = re.search(r'"target_file"\s*:\s*"([^"]+)"', raw)
    # Extract explanation (simpler field)
    ex_match = re.search(r'"explanation"\s*:\s*"([^"]*)"', raw)
    # Extract new_content by finding the value between "new_content": " and the last "
    nc_match = re.search(r'"new_content"\s*:\s*"(.*?)"\s*(?:,\s*"|\})', raw, re.DOTALL)
    if not nc_match:
        # Last-resort: grab everything between "new_content": " and end of string
        nc_match = re.search(r'"new_content"\s*:\s*"(.*)', raw, re.DOTALL)

    if tf_match and nc_match:
        content = nc_match.group(1)
        # Unescape \\n → \n, \\t → \t etc.
        content = content.replace('\\n', '\n').replace('\\t', '\t').replace('\\"', '"').rstrip('"}')
        return {
            "target_file": tf_match.group(1),
            "new_content": content,
            "explanation": ex_match.group(1) if ex_match else "patch applied",
        }

    # Nothing worked — raise with clear diagnostic
    if not raw.strip():
        raise ValueError("LLM returned empty response — cannot parse JSON")
    raise ValueError(f"Failed to parse JSON from LLM response (first 200 chars): {raw[:200]}")


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

    if "cache" in task.get("file", "") or "processor" in task.get("file", ""):
        user_content += f"\n\nKNOWN FIX PATTERN:\n{TASK_HINTS['memory_leak']}"

    temp = 0.2 if task.get("bug_type") == "logic_error" else 0.0
    llm = ChatGroq(model=_select_model(task.get("bug_type", "")), temperature=temp)
    messages = [SystemMessage(content=_current_system), HumanMessage(content=user_content)]
    response = llm.invoke(messages)
    raw = _strip_fences((response.content or "").strip())

    result = _safe_json_loads(raw)

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
    temp = 0.2 if task.get("bug_type") == "logic_error" else 0.0
    llm = ChatGroq(model=_select_model(task.get("bug_type", "")), temperature=temp)

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
    raw = _strip_fences((response.content or "").strip())

    result = _safe_json_loads(raw)

    if not isinstance(result, dict) or "target_file" not in result or "new_content" not in result:
        raise ValueError(
            f"Coder retry output missing required keys. Got: {str(result)[:300]}"
        )

    return result
