from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ctf_harness.protocol import ReportKind, WorkerAssignment, WorkerReport
from ctf_harness.worker.runner import ReportCallback

WORKER_INSTRUCTIONS = """You are an autonomous CTF worker assigned exactly one challenge.
Work only inside the assigned workspace. Perform the analysis and verification yourself.
Report meaningful checkpoints, completion, or terminal failures, not individual tool calls.
A failed report includes any condition you cannot resolve autonomously. A completed report
must describe real reproduced evidence; model prose alone is not proof. Return only JSON
matching the supplied output schema."""

REPORT_SCHEMA = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": [kind.value for kind in ReportKind]},
        "summary": {"type": "string"},
        "artifacts": {"type": "array", "items": {"type": "string"}},
        "flag_candidate": {"type": ["string", "null"]},
    },
    "required": ["kind", "summary", "artifacts", "flag_candidate"],
    "additionalProperties": False,
}


def _report_from_response(assignment: WorkerAssignment, text: str | None) -> WorkerReport:
    if not text:
        raise ValueError("Codex worker returned no final response")
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        stripped = "\n".join(lines[1:-1])
    payload = json.loads(stripped)
    return WorkerReport(
        run_id=assignment.run_id,
        worker_id=assignment.worker_id,
        challenge_id=assignment.challenge_id,
        kind=ReportKind(payload["kind"]),
        summary=str(payload["summary"]),
        artifacts=tuple(str(item) for item in payload.get("artifacts", [])),
        flag_candidate=payload.get("flag_candidate"),
    )


class CodexWorkerRunner:
    """Run one Codex SDK thread for one challenge/model swarm."""

    def __init__(
        self,
        sdk_factory: Callable[[], Any] | None = None,
        max_turns: int = 8,
    ) -> None:
        if max_turns < 1:
            raise ValueError("max_turns must be positive")
        self._sdk_factory = sdk_factory
        self.max_turns = max_turns

    async def run(self, assignment: WorkerAssignment, report: ReportCallback) -> WorkerReport:
        workspace = Path(assignment.workspace_uri).resolve()
        workspace.mkdir(parents=True, exist_ok=True)

        sandbox: Any = None
        factory = self._sdk_factory
        if factory is None:
            from openai_codex import AsyncCodex, Sandbox

            factory = AsyncCodex
            sandbox = Sandbox.workspace_write

        try:
            async with factory() as client:
                kwargs: dict[str, Any] = {
                    "model": assignment.model,
                    "cwd": str(workspace),
                    "developer_instructions": WORKER_INSTRUCTIONS,
                }
                if sandbox is not None:
                    kwargs["sandbox"] = sandbox
                thread = await client.thread_start(**kwargs)
                connection = (
                    f"{assignment.host}:{assignment.port}"
                    if assignment.host is not None and assignment.port is not None
                    else "not provided"
                )
                prompt = (
                    "You received one CTF challenge. Interpret the challenge data and decide "
                    "your own analysis and solving strategy.\n"
                    f"Challenge ID: {assignment.challenge_id}\n"
                    f"Title: {assignment.challenge_title}\n"
                    f"Category: {assignment.challenge_category}\n"
                    f"Description: {assignment.challenge_description}\n"
                    f"Connection: {connection}\n"
                    f"Workspace: {workspace}\n"
                    "Begin autonomously and return the next meaningful report."
                )

                for _ in range(self.max_turns):
                    result = await thread.run(prompt, output_schema=REPORT_SCHEMA)
                    worker_report = _report_from_response(assignment, result.final_response)
                    await report(worker_report)
                    if worker_report.kind in {ReportKind.COMPLETED, ReportKind.FAILED}:
                        return worker_report
                    prompt = "Continue autonomously and return the next meaningful report."
        except Exception as exc:
            failed = WorkerReport(
                assignment.run_id,
                assignment.worker_id,
                assignment.challenge_id,
                ReportKind.FAILED,
                f"worker runtime failed: {type(exc).__name__}: {exc}",
            )
            await report(failed)
            return failed

        failed = WorkerReport(
            assignment.run_id,
            assignment.worker_id,
            assignment.challenge_id,
            ReportKind.FAILED,
            f"worker exceeded the {self.max_turns}-turn harness limit",
        )
        await report(failed)
        return failed
