from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ctf_harness.domain import Challenge
from ctf_harness.protocol import Feedback, ReportKind, WorkerAssignment, WorkerReport, to_json

COORDINATOR_INSTRUCTIONS = """You are the long-lived coordinator for a CTF harness.
You classify challenges, create one high-level assignment per challenge, and review
coarse worker reports. Never micromanage shell commands or solve the challenge
tool-by-tool. A swarm contains exactly one worker model. Return only JSON matching
the supplied output schema."""

ASSIGNMENT_SCHEMA = {
    "type": "object",
    "properties": {
        "objective": {"type": "string"},
        "profile": {"type": "string"},
    },
    "required": ["objective", "profile"],
    "additionalProperties": False,
}

FEEDBACK_SCHEMA = {
    "type": "object",
    "properties": {"directive": {"type": ["string", "null"]}},
    "required": ["directive"],
    "additionalProperties": False,
}


def _decode_json(text: str | None) -> dict[str, Any]:
    if not text:
        raise ValueError("Codex returned no final response")
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        stripped = "\n".join(lines[1:-1])
    value = json.loads(stripped)
    if not isinstance(value, dict):
        raise ValueError("Codex response must be a JSON object")
    return value


class CodexCoordinatorBackend:
    """One long-lived Codex SDK thread for run-level coordination."""

    def __init__(
        self,
        model: str,
        cwd: Path,
        sdk_factory: Callable[[], Any] | None = None,
    ) -> None:
        self.model = model
        self.cwd = cwd.resolve()
        self._sdk_factory = sdk_factory
        self._client_cm: Any | None = None
        self._client: Any | None = None
        self._thread: Any | None = None
        self._turn_lock = asyncio.Lock()
        self.thread_id: str | None = None

    async def start(self) -> None:
        if self._thread is not None:
            return
        sandbox: Any = None
        if self._sdk_factory is None:
            from openai_codex import AsyncCodex, Sandbox

            self._sdk_factory = AsyncCodex
            sandbox = Sandbox.read_only
        self._client_cm = self._sdk_factory()
        self._client = await self._client_cm.__aenter__()
        kwargs: dict[str, Any] = {
            "model": self.model,
            "cwd": str(self.cwd),
            "base_instructions": COORDINATOR_INSTRUCTIONS,
        }
        if sandbox is not None:
            kwargs["sandbox"] = sandbox
        self._thread = await self._client.thread_start(**kwargs)
        self.thread_id = getattr(self._thread, "id", None)

    async def stop(self) -> None:
        if self._client_cm is not None:
            await self._client_cm.__aexit__(None, None, None)
        self._client_cm = None
        self._client = None
        self._thread = None

    async def create_assignment(
        self,
        run_id: str,
        worker_id: str,
        challenge: Challenge,
        workspace: Path,
        worker_model: str,
    ) -> WorkerAssignment:
        await self.start()
        prompt = (
            "Create the initial assignment for this challenge. The worker acts autonomously "
            "and reports only meaningful checkpoints.\n"
            f"challenge_id={challenge.id}\n"
            f"title={challenge.title}\ncategory={challenge.category}\n"
            f"description={challenge.description}\nworker_model={worker_model}"
        )
        async with self._turn_lock:
            result = await self._thread.run(prompt, output_schema=ASSIGNMENT_SCHEMA)
        payload = _decode_json(result.final_response)
        return WorkerAssignment(
            run_id=run_id,
            worker_id=worker_id,
            challenge_id=challenge.id,
            objective=str(payload["objective"]),
            workspace_uri=str(workspace),
            profile=str(payload["profile"]),
            model=worker_model,
        )

    async def review_report(self, report: WorkerReport) -> Feedback | None:
        await self.start()
        prompt = (
            "Review this coarse-grained worker report. Return a directive only when high-level "
            "feedback is useful; otherwise return null. Do not prescribe every shell command.\n"
            f"{to_json(report)}"
        )
        async with self._turn_lock:
            result = await self._thread.run(prompt, output_schema=FEEDBACK_SCHEMA)
        payload = _decode_json(result.final_response)
        directive = payload.get("directive")
        if report.kind in {ReportKind.COMPLETED, ReportKind.FAILED} or not directive:
            return None
        return Feedback(report.run_id, report.worker_id, str(directive))