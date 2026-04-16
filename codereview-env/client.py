import requests
from typing import Tuple, Dict, Any

from models import CodeObservation, AgentAction, StepResponse


class CodeReviewEnv:
    """HTTP client wrapper for the CodeReview-Env FastAPI server."""

    def __init__(self, base_url: str = "http://localhost:7860"):
        self.base_url = base_url.rstrip("/")

    def reset(self, task_id: str = None) -> CodeObservation:
        """
        Start a new episode.

        Parameters
        ----------
        task_id : str, optional
            Specific task to load (e.g. ``"task_3_pr"``).  When omitted the
            server cycles tasks in round-robin order.
        """
        payload = {"task_id": task_id} if task_id else {}
        response = requests.post(
            f"{self.base_url}/reset",
            json=payload,
            timeout=30,
        )
        response.raise_for_status()
        return CodeObservation.model_validate(response.json())

    def step(self, action: AgentAction) -> Tuple[CodeObservation, float, bool, Dict[str, Any]]:
        payload = action.model_dump_json()
        response = requests.post(
            f"{self.base_url}/step",
            data=payload,
            headers={"Content-Type": "application/json"},
            timeout=60,
        )
        response.raise_for_status()
        data = response.json()
        obs = CodeObservation.model_validate(data["observation"])
        return obs, data["reward"], data["done"], data["info"]

    def state(self) -> Dict[str, Any]:
        response = requests.get(f"{self.base_url}/state", timeout=10)
        response.raise_for_status()
        return response.json()

    def health(self) -> bool:
        try:
            response = requests.get(f"{self.base_url}/health", timeout=5)
            return response.status_code == 200
        except Exception:
            return False
