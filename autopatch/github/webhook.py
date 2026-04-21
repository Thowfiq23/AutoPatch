"""
autopatch/github/webhook.py
----------------------------
FastAPI router that receives GitHub webhook events (push, pull_request),
verifies the HMAC-SHA256 signature, and queues fix jobs as background tasks.

Mount in server.py:
    from autopatch.github.webhook import router as webhook_router
    app.include_router(webhook_router)
"""

import hashlib
import hmac
import logging
import os

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request

from autopatch.github.job_handler import handle_pull_request, handle_push

router = APIRouter()
logger = logging.getLogger(__name__)


def _verify_signature(payload: bytes, signature: str) -> bool:
    secret = os.getenv("WEBHOOK_SECRET", "").encode()
    if not secret:
        logger.warning("[WEBHOOK] WEBHOOK_SECRET not set — skipping signature check")
        return True
    expected = "sha256=" + hmac.new(secret, payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


@router.post("/webhook/github")
async def github_webhook(request: Request, background_tasks: BackgroundTasks):
    """
    Receive a GitHub webhook event.

    - Verifies HMAC-SHA256 signature.
    - Dispatches push or pull_request events as background tasks.
    - Always returns 200 immediately (GitHub requires fast response).
    - Never raises — logs errors and returns 200 always.
    """
    try:
        payload_bytes = await request.body()
        sig = request.headers.get("X-Hub-Signature-256", "")

        if not _verify_signature(payload_bytes, sig):
            logger.warning("[WEBHOOK] Invalid signature — rejecting")
            raise HTTPException(status_code=401, detail="Invalid signature")

        event = request.headers.get("X-GitHub-Event", "")
        payload = await request.json()

        repo = payload.get("repository", {}).get("full_name", "unknown")
        logger.info("[WEBHOOK] event=%s repo=%s", event, repo)

        if event == "push":
            background_tasks.add_task(handle_push, payload)
        elif event == "pull_request" and payload.get("action") == "opened":
            background_tasks.add_task(handle_pull_request, payload)
        else:
            logger.debug("[WEBHOOK] Ignoring event=%s action=%s", event, payload.get("action"))

        return {"status": "queued", "event": event, "repo": repo}

    except HTTPException:
        raise
    except Exception as exc:
        logger.error("[WEBHOOK] Unexpected error: %s", exc)
        return {"status": "error", "detail": str(exc)}
