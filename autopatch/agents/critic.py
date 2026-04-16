"""
autopatch/agents/critic.py
---------------------------
Critic agent: reviews a proposed patch before it is applied to the environment.

Quality gate between the Coder and the environment. Prevents wasted patch_file
calls and 0.0 rewards from syntactically correct but semantically wrong patches.

All P0 checks are deterministic rule-based (regex/string) — they never rely on
the LLM so they are 100% reliable. The LLM is called only for a semantic
confidence score when all rules pass.

Never raises — returns {approved: False, feedback: 'error', confidence: 0.0}
on any unexpected failure.
"""

import logging
import os
import re

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Regex patterns for static checks
# ---------------------------------------------------------------------------

# Detects f-string SQL: f"...{variable}..." or f'...{variable}...'
_RE_FSTRING_SQL = re.compile(
    r'f["\'].*?(?:SELECT|INSERT|UPDATE|DELETE|WHERE|FROM).*?\{[^}]+\}.*?["\']',
    re.IGNORECASE | re.DOTALL,
)

# Detects parameterized SQL tuple: execute("...", (param,)) or execute("...", [param])
_RE_PARAM_SQL = re.compile(
    r'\.execute\s*\(\s*["\'].*?["\'],\s*[\(\[]',
    re.IGNORECASE | re.DOTALL,
)

# Detects weak hashing
_RE_WEAK_CRYPTO = re.compile(r'\b(hashlib\.md5|hashlib\.sha1|md5\s*\(|sha1\s*\()\b', re.IGNORECASE)

# Detects strong hashing
_RE_STRONG_CRYPTO = re.compile(r'\b(pbkdf2_hmac|bcrypt|argon2)\b', re.IGNORECASE)

# Detects hardcoded secrets as literal string values (not variable names, not os.getenv)
# Matches patterns like: sk_abc123, pk_xyz789, key_something — as quoted string literals
_RE_HARDCODED_SECRET = re.compile(
    r'''["'](sk_[a-z0-9]+|pk_[a-z0-9]+|key_[a-z0-9]+)["']''',
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Public critique() function
# ---------------------------------------------------------------------------

def critique(original: str, patch: dict, bug_type: str) -> dict:
    """
    Review a proposed patch before it is applied.

    Parameters
    ----------
    original : str
        Current content of the file (before patching).
    patch : dict
        Coder output: {target_file, new_content, explanation}.
    bug_type : str
        The bug type this patch is supposed to fix.

    Returns
    -------
    dict
        {approved: bool, feedback: str, confidence: float}
        Never raises — returns {approved: False, feedback: 'error', confidence: 0.0}
        on any unexpected failure.
    """
    try:
        new_content = patch.get("new_content", "")

        # --- P0: SQL injection check ---
        if bug_type == "sql_injection" or _RE_FSTRING_SQL.search(new_content):
            result = _check_sql(new_content, bug_type)
            if result is not None:
                return result

        # --- P0: Weak crypto check ---
        if bug_type == "weak_crypto" or _RE_WEAK_CRYPTO.search(new_content):
            result = _check_crypto(new_content, bug_type)
            if result is not None:
                return result

        # --- P0: Hardcoded secret check ---
        if bug_type == "hardcoded_secret":
            result = _check_secrets(new_content)
            if result is not None:
                return result
        else:
            # Still reject hardcoded secrets even if not the declared bug type
            secret_match = _RE_HARDCODED_SECRET.search(new_content)
            if secret_match:
                return {
                    "approved": False,
                    "feedback": (
                        f"Patch introduces a hardcoded secret literal: '{secret_match.group(1)}'. "
                        "Use os.getenv() instead."
                    ),
                    "confidence": 0.95,
                }

        # --- P0: Async error check ---
        if bug_type == "async_error":
            result = _check_async(new_content)
            if result is not None:
                return result

        # --- All rules passed: compute heuristic confidence ---
        confidence = _heuristic_confidence(original, new_content, bug_type)

        if confidence < 0.5:
            logger.warning(
                "[CRITIC WARNING] Low-confidence approval (%.2f) for bug_type=%s",
                confidence,
                bug_type,
            )

        return {
            "approved": True,
            "feedback": "Patch passes all static checks.",
            "confidence": confidence,
        }

    except Exception as exc:
        logger.error("[CRITIC] Unexpected error: %s", exc)
        return {"approved": False, "feedback": "error", "confidence": 0.0}


# ---------------------------------------------------------------------------
# Individual check helpers — return dict on rejection, None to continue
# ---------------------------------------------------------------------------

def _check_sql(new_content: str, bug_type: str) -> dict | None:
    """Reject f-string SQL. Approve only if parameterized tuple is present."""
    if _RE_FSTRING_SQL.search(new_content):
        return {
            "approved": False,
            "feedback": (
                "Patch still uses an f-string to build SQL queries. "
                "Use parameterized queries: cursor.execute('SELECT ... WHERE x = %s', (value,))"
            ),
            "confidence": 0.99,
        }

    if bug_type == "sql_injection" and not _RE_PARAM_SQL.search(new_content):
        return {
            "approved": False,
            "feedback": (
                "SQL injection fix must use parameterized queries with a tuple argument: "
                "cursor.execute('...', (param,)). No such pattern was found."
            ),
            "confidence": 0.85,
        }

    return None


def _check_crypto(new_content: str, bug_type: str) -> dict | None:
    """Reject MD5/SHA1. Approve only if strong crypto is present."""
    if _RE_WEAK_CRYPTO.search(new_content):
        return {
            "approved": False,
            "feedback": (
                "Patch still uses MD5 or SHA1. "
                "Use hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 100000) instead."
            ),
            "confidence": 0.99,
        }

    if bug_type == "weak_crypto" and not _RE_STRONG_CRYPTO.search(new_content):
        return {
            "approved": False,
            "feedback": (
                "Weak crypto fix must use pbkdf2_hmac, bcrypt, or argon2. "
                "No strong hashing function was found in the patch."
            ),
            "confidence": 0.85,
        }

    return None


def _check_secrets(new_content: str) -> dict | None:
    """Reject hardcoded API key literals."""
    match = _RE_HARDCODED_SECRET.search(new_content)
    if match:
        return {
            "approved": False,
            "feedback": (
                f"Patch contains a hardcoded secret literal: '{match.group(1)}'. "
                "Replace it with os.getenv('VARIABLE_NAME') and ensure import os is present."
            ),
            "confidence": 0.99,
        }
    # Also ensure os.getenv is used somewhere if this is a secret fix
    if "os.getenv" not in new_content and "os.environ" not in new_content:
        return {
            "approved": False,
            "feedback": (
                "Hardcoded secret fix must use os.getenv() or os.environ to load the value "
                "from the environment. Neither was found in the patch."
            ),
            "confidence": 0.80,
        }
    return None


def _check_async(new_content: str) -> dict | None:
    """Reject if async functions are missing async def or await."""
    has_async_def = "async def" in new_content
    has_await = "await" in new_content

    if not has_async_def:
        return {
            "approved": False,
            "feedback": (
                "Async error fix requires 'async def' for the function declaration. "
                "Found 'def' without 'async'."
            ),
            "confidence": 0.99,
        }

    if not has_await:
        return {
            "approved": False,
            "feedback": (
                "Async error fix requires 'await' before async calls inside the function. "
                "No 'await' keyword was found."
            ),
            "confidence": 0.99,
        }

    return None


def _heuristic_confidence(original: str, new_content: str, bug_type: str) -> float:
    """
    Compute a heuristic confidence score in [0.0, 1.0] based on structural signals.

    This is intentionally fast and deterministic — no LLM call needed.
    """
    score = 0.7  # base pass score

    # Content was actually changed
    if new_content.strip() != original.strip():
        score += 0.1

    # Patch is non-trivially sized (not empty or just whitespace change)
    if len(new_content.strip()) > 50:
        score += 0.05

    # Bug-type specific bonuses for expected patterns
    if bug_type == "sql_injection" and _RE_PARAM_SQL.search(new_content):
        score += 0.1
    elif bug_type == "weak_crypto" and _RE_STRONG_CRYPTO.search(new_content):
        score += 0.1
    elif bug_type == "hardcoded_secret" and "os.getenv" in new_content:
        score += 0.1
    elif bug_type == "async_error" and "async def" in new_content and "await" in new_content:
        score += 0.1
    elif bug_type == "logic_error":
        score += 0.05  # we can't easily verify logic fixes statically

    return min(score, 1.0)
