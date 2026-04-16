"""
autopatch/agents/evolver.py
----------------------------
Evolver agent: analyses reward trajectories and rewrites the Coder's system
prompt to improve future performance.

The self-improvement engine — this is what makes AutoPatch unique. Agents get
measurably better over time. The reward curve going up is the demo.

Trigger rule: runs only on episodes where episode_number % 5 == 0.
Minimum 3 stored trajectories are required before any evolution happens.
"""

import logging
import os

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_groq import ChatGroq

from autopatch.agents import coder, memory

logger = logging.getLogger(__name__)

_MODEL = os.getenv("MODEL_NAME", "llama-3.3-70b-versatile")

# Minimum new prompt length — protects against corrupted/empty LLM responses
_MIN_PROMPT_LENGTH = 200

# System prompt given to the LLM when asking it to evolve the coder prompt.
# Instructs it to PRESERVE all existing rules and only ADD new guidance.
_EVOLVER_SYSTEM = """You are a prompt engineer specialising in code repair agents.
You will receive the current system prompt used by a Python bug-fixing agent, along
with performance statistics from recent episodes.

Your task: rewrite the system prompt to improve future performance based on the data.

STRICT RULES:
1. Keep ALL existing bug-type rules intact (sql_injection, weak_crypto, hardcoded_secret, async_error, logic_error).
2. Only ADD new guidance — never remove or weaken existing instructions.
3. The output must be the complete new system prompt as plain text (no JSON, no markdown).
4. The new prompt must be longer than 200 characters.
5. Focus on patterns from episodes where the score was LOW — what strategies might have helped?"""


def _summarise_trajectories(trajectories: list) -> str:
    """
    Convert raw trajectory records into a concise performance summary for the LLM.

    For each trajectory, computes:
      - steps_to_first_reward: index of first reward > 0 (or "never")
      - total_steps: length of rewards list
      - final_score: max reward in the episode
    """
    lines = []
    for i, traj in enumerate(trajectories, 1):
        rewards = traj.get("rewards", [])
        episode = traj.get("episode", i)
        score = traj.get("score", 0.0)

        total_steps = len(rewards)
        first_reward_idx = next(
            (idx for idx, r in enumerate(rewards) if r > 0), None
        )
        steps_to_first = (first_reward_idx + 1) if first_reward_idx is not None else "never"

        lines.append(
            f"  Episode {episode}: steps={total_steps}, "
            f"steps_to_first_reward={steps_to_first}, "
            f"final_score={score:.3f}, "
            f"rewards=[{', '.join(f'{r:.2f}' for r in rewards)}]"
        )
    return "\n".join(lines)


def maybe_evolve(run_id: str, episode_number: int) -> None:
    """
    Analyse recent trajectories and rewrite the Coder's system prompt if warranted.

    Trigger condition: episode_number % 5 == 0
    Minimum data: at least 3 stored trajectories for this run_id.

    On success: calls coder.set_system() with the new evolved prompt.
    On failure or insufficient data: no-op (never raises).

    Parameters
    ----------
    run_id : str
        The current run identifier — used to retrieve trajectories from Memory.
    episode_number : int
        The current episode number. Evolution only fires when this is divisible by 5.
    """
    # --- Trigger check ---
    if episode_number % 5 != 0:
        return

    try:
        trajectories = memory.get_trajectories(run_id)

        # --- Minimum data check ---
        if len(trajectories) < 3:
            memory.store_log(
                run_id,
                f"[EVOLVER] episode={episode_number} skipped — "
                f"only {len(trajectories)} trajectory/ies stored (need ≥3)",
            )
            return

        n = len(trajectories)
        memory.store_log(
            run_id,
            f"[EVOLVER] episode={episode_number} analysing {n} trajectories",
        )
        logger.info("[EVOLVER] episode=%d analysing %d trajectories", episode_number, n)

        # --- Build LLM prompt ---
        current_prompt = coder.get_system()
        summary = _summarise_trajectories(trajectories)

        user_content = (
            f"Current coder system prompt:\n"
            f"---\n{current_prompt}\n---\n\n"
            f"Performance data from the last {n} episodes:\n{summary}\n\n"
            "Rewrite the system prompt to improve the agent's performance. "
            "Remember: preserve all existing bug-type rules and only add new guidance."
        )

        llm = ChatGroq(model=_MODEL, temperature=0.3)
        messages = [
            SystemMessage(content=_EVOLVER_SYSTEM),
            HumanMessage(content=user_content),
        ]
        response = llm.invoke(messages)
        new_prompt = (response.content or "").strip()

        # --- Prompt validation ---
        if len(new_prompt) < _MIN_PROMPT_LENGTH:
            memory.store_log(
                run_id,
                f"[EVOLVER] episode={episode_number} discarded prompt — "
                f"too short ({len(new_prompt)} chars, need >{_MIN_PROMPT_LENGTH})",
            )
            logger.warning(
                "[EVOLVER] Discarded evolved prompt: too short (%d chars)", len(new_prompt)
            )
            return

        # --- Apply the new prompt ---
        coder.set_system(new_prompt)
        memory.store_log(
            run_id,
            f"[EVOLVER] episode={episode_number} prompt updated "
            f"({len(new_prompt)} chars, from {len(current_prompt)} chars)",
        )
        logger.info(
            "[EVOLVER] episode=%d prompt updated (%d→%d chars)",
            episode_number,
            len(current_prompt),
            len(new_prompt),
        )

    except Exception as exc:
        logger.error("[EVOLVER] Unexpected error at episode %d: %s", episode_number, exc)
        # Never raise — a failed evolution must not crash the episode loop
