"""
autopatch/server.py
--------------------
FastAPI server that exposes the AutoPatch system as an HTTP API for the
dashboard and external consumers.

Endpoints:
  POST /run                  — start a run, return run_id immediately
  GET  /status/{run_id}      — live episode progress + scores
  GET  /rewards/{run_id}     — full per-step trajectory data
  GET  /logs/{run_id}        — Server-Sent Events log stream
  GET  /health               — liveness probe

Run with:
  uvicorn autopatch.server:app --port 8000 --reload
"""

import asyncio
import logging
import os
import uuid
from typing import Any, Dict, List

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from autopatch.agents import memory
from autopatch import orchestrator

logger = logging.getLogger(__name__)

app = FastAPI(title="AutoPatch API", version="1.0.0")

# Allow all origins so the React dashboard on localhost:5173 can call us
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# In-memory run registry
# { run_id: { "status": "running"|"done", "episode": int, "scores": list[float] } }
# ---------------------------------------------------------------------------
_active_runs: Dict[str, Dict[str, Any]] = {}


# ---------------------------------------------------------------------------
# POST /run
# ---------------------------------------------------------------------------

@app.post("/run")
async def start_run(episodes: int = 10):
    """
    Start a new AutoPatch run in the background.

    Returns {run_id, episodes} immediately (< 100 ms).
    Episodes run sequentially in a background asyncio task.
    """
    try:
        run_id = str(uuid.uuid4())[:8]
        _active_runs[run_id] = {
            "status": "running",
            "episode": 0,
            "scores": [],
        }
        memory.store_log(run_id, f"[AUTOPATCH] run_id={run_id} episodes={episodes} starting")

        asyncio.create_task(_run_episodes(run_id, episodes))

        return {"run_id": run_id, "episodes": episodes}
    except Exception as exc:
        logger.error("/run error: %s", exc)
        return JSONResponse(status_code=500, content={"error": str(exc)})


async def _run_episodes(run_id: str, total: int) -> None:
    """Background task: run `total` episodes sequentially, updating _active_runs."""
    for ep in range(1, total + 1):
        _active_runs[run_id]["episode"] = ep
        memory.store_log(run_id, f"[AUTOPATCH] Starting episode {ep}/{total}")
        try:
            score = await asyncio.to_thread(
                orchestrator.run_episode, run_id, ep
            )
        except Exception as exc:
            logger.error("run_id=%s ep=%d crashed: %s", run_id, ep, exc)
            score = 0.0

        _active_runs[run_id]["scores"].append(score)
        memory.store_log(run_id, f"[SUMMARY] episode={ep} score={score:.3f}")

    _active_runs[run_id]["status"] = "done"
    memory.store_log(run_id, f"[AUTOPATCH] run_id={run_id} complete")


# ---------------------------------------------------------------------------
# GET /status/{run_id}
# ---------------------------------------------------------------------------

@app.get("/status/{run_id}")
async def get_status(run_id: str):
    """
    Return live run progress.

    Response schema:
      status        — "running" | "done" | "not_found"
      episode       — current episode number
      average_score — mean of completed episode scores
      scores        — list of all episode scores so far
    """
    try:
        run = _active_runs.get(run_id)
        if run is None:
            return {"status": "not_found", "episode": 0, "average_score": 0.0, "scores": []}

        scores = run["scores"]
        avg = sum(scores) / len(scores) if scores else 0.0
        return {
            "status": run["status"],
            "episode": run["episode"],
            "average_score": round(avg, 4),
            "scores": scores,
        }
    except Exception as exc:
        logger.error("/status/%s error: %s", run_id, exc)
        return JSONResponse(status_code=500, content={"error": str(exc)})


# ---------------------------------------------------------------------------
# GET /rewards/{run_id}
# ---------------------------------------------------------------------------

@app.get("/rewards/{run_id}")
async def get_rewards(run_id: str):
    """
    Return full per-step trajectory data for reward charting.

    Calls memory.get_trajectories() and returns the list directly.
    """
    try:
        trajectories = memory.get_trajectories(run_id)
        return {"run_id": run_id, "trajectories": trajectories}
    except Exception as exc:
        logger.error("/rewards/%s error: %s", run_id, exc)
        return JSONResponse(status_code=500, content={"error": str(exc)})


# ---------------------------------------------------------------------------
# GET /logs/{run_id}  — Server-Sent Events
# ---------------------------------------------------------------------------

@app.get("/logs/{run_id}")
async def stream_logs(run_id: str):
    """
    Stream log lines as Server-Sent Events.

    Polls memory.get_logs() every 0.5 s, forwarding new lines.
    Sends 'data: [STREAM_END]\\n\\n' when the run is done and all logs have
    been sent, then closes the stream.

    EventSource clients should close on [STREAM_END] to avoid memory leaks.
    """
    async def _event_generator():
        sent = 0
        while True:
            logs = memory.get_logs(run_id)
            while sent < len(logs):
                line = logs[sent].replace("\n", " ")
                yield f"data: {line}\n\n"
                sent += 1

            run = _active_runs.get(run_id)
            # Run is done and we've forwarded every log line
            if run is not None and run["status"] == "done" and sent >= len(logs):
                yield "data: [STREAM_END]\n\n"
                break

            # run_id not yet registered — yield a heartbeat and keep waiting
            await asyncio.sleep(0.5)

    return StreamingResponse(
        _event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# GET /health
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    """
    Liveness probe — always returns {status: healthy}.
    Does NOT check Redis or the CodeReview-Env; must respond even if they're down.
    """
    return {"status": "healthy"}


# ---------------------------------------------------------------------------
# Global exception handler — ensure no raw tracebacks leak to clients
# ---------------------------------------------------------------------------

@app.exception_handler(Exception)
async def _global_exception_handler(request, exc):
    logger.error("Unhandled exception on %s: %s", request.url.path, exc)
    return JSONResponse(status_code=500, content={"error": str(exc)})
