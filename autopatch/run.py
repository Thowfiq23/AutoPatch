"""
autopatch/run.py
-----------------
CLI entry point for running AutoPatch from the terminal without the dashboard.

Usage:
    python -m autopatch.run
    python -m autopatch.run --episodes 5
    python -m autopatch.run --episodes 5 --env-url http://localhost:7860

Stdout format (matches inference.py [START]/[STEP]/[END] convention):
    [AUTOPATCH] Starting run_id=<id> episodes=<n> env=<url>
    [SUMMARY]   episode=1 score=0.850
    [SUMMARY]   episode=2 score=0.900
    [AUTOPATCH] Complete. run_id=<id>

All internal agent/LangGraph logs go to stderr so stdout stays parseable.

ENV_URL is written to os.environ BEFORE the orchestrator is imported so every
agent module picks up the CLI value, not the .env default.
"""

import argparse
import logging
import os
import sys
import uuid


def _load_dotenv() -> None:
    """Load .env from the project root if python-dotenv is available."""
    try:
        from dotenv import load_dotenv
        # Walk up from this file to find a .env
        here = os.path.dirname(os.path.abspath(__file__))
        for _ in range(3):
            candidate = os.path.join(here, ".env")
            if os.path.isfile(candidate):
                load_dotenv(candidate)
                break
            here = os.path.dirname(here)
    except ImportError:
        pass  # python-dotenv is optional


def _health_check(env_url: str) -> bool:
    """Return True if the environment responds to GET /health with HTTP 200."""
    import httpx
    try:
        resp = httpx.get(f"{env_url}/health", timeout=5.0)
        return resp.status_code == 200
    except Exception:
        return False


def main() -> None:
    _load_dotenv()

    parser = argparse.ArgumentParser(
        description="AutoPatch — Self-Improving Multi-Agent Code Repair",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--episodes",
        type=int,
        default=3,
        help="Number of episodes to run",
    )
    parser.add_argument(
        "--env-url",
        type=str,
        default=os.getenv("ENV_URL", "http://localhost:7860"),
        help="Base URL of the CodeReview-Env instance",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="WARNING",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Log level for internal agent output (goes to stderr)",
    )
    args = parser.parse_args()

    # Route internal logs to stderr — stdout must contain only [AUTOPATCH]/[SUMMARY] lines
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )

    # ── ENV_URL must be set BEFORE importing the orchestrator ──────────────
    # All agents read ENV_URL at call-time via os.getenv, but setting it here
    # ensures any module-level reads also pick up the CLI value.
    os.environ["ENV_URL"] = args.env_url

    # ── Health check — exit 1 if environment is unreachable ────────────────
    if not _health_check(args.env_url):
        print(
            f"[AUTOPATCH] ERROR: CodeReview-Env health check failed at {args.env_url}\n"
            "           Make sure the environment is running: "
            "docker run -p 7860:7860 codereview-env",
            file=sys.stderr,
            flush=True,
        )
        sys.exit(1)

    # ── Deferred import — ENV_URL is in os.environ before any module reads it
    from autopatch import orchestrator  # noqa: E402  (intentional late import)

    run_id = str(uuid.uuid4())[:8]

    print(
        f"[AUTOPATCH] Starting run_id={run_id} episodes={args.episodes} env={args.env_url}",
        flush=True,
    )

    for ep in range(1, args.episodes + 1):
        try:
            score = orchestrator.run_episode(run_id=run_id, episode=ep)
        except KeyboardInterrupt:
            print("\n[AUTOPATCH] Interrupted by user.", flush=True)
            sys.exit(0)
        except Exception as exc:
            logging.getLogger(__name__).error("Episode %d crashed: %s", ep, exc)
            score = 0.0

        print(f"[SUMMARY] episode={ep} score={score:.3f}", flush=True)

    print(f"[AUTOPATCH] Complete. run_id={run_id}", flush=True)
    sys.exit(0)


if __name__ == "__main__":
    main()
