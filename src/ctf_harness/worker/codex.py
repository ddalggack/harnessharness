from __future__ import annotations

import json
import asyncio
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from ctf_harness.protocol import ReportKind, WorkerAssignment, WorkerReport
from ctf_harness.worker.runner import ReportCallback, ReportDecision
from ctf_harness.writeups import find_solver

WORKER_INSTRUCTIONS = """You are an autonomous CTF worker assigned exactly one challenge.
Work only inside the assigned workspace. Perform the analysis and verification yourself.
Report meaningful checkpoints, completion, or terminal failures, not individual tool calls.
A failed report includes any condition you cannot resolve autonomously. Report a discovered
flag as flag_candidate so the coordinator can validate it. Use completed only when no flag
is required. Before reporting a flag candidate or completion, create a non-empty reusable
solver program in the workspace, preferably solver.py, that reproduces the solution from
a clean run, and include it in artifacts. A completed report must describe real reproduced evidence. Return only JSON
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


def _json_value(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", by_alias=True)
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    return value


def _truncate(value: str | None, limit: int = 8000) -> str | None:
    if value is None or len(value) <= limit:
        return value
    return value[:limit] + f"\n… <truncated {len(value) - limit} chars>"


def _reasoning_summary(root: Any) -> str | None:
    summary = getattr(root, "summary", None)
    if not summary:
        return None
    return "\n".join(str(part) for part in summary)


def _item_activity(root: Any, status: str, intent: str | None) -> tuple[str, dict[str, Any]]:
    item_type = str(getattr(root, "type", "unknown"))
    common: dict[str, Any] = {"item_type": item_type, "status": status}
    if item_type == "agentMessage":
        return "worker.message", {**common, "role": "assistant", "content": getattr(root, "text", "")}
    if item_type == "userMessage":
        return "worker.message", {**common, "role": "user", "content": _json_value(getattr(root, "content", []))}
    if item_type == "reasoning":
        return "worker.reasoning", {
            **common,
            "summary": _reasoning_summary(root) or "reasoning summary unavailable",
        }
    if item_type == "commandExecution":
        return "worker.tool", {
            **common,
            "tool": "shell",
            "intent": intent,
            "command": getattr(root, "command", None),
            "cwd": str(getattr(root, "cwd", "")),
            "exit_code": getattr(root, "exit_code", None),
            "duration_ms": getattr(root, "duration_ms", None),
            "output": _truncate(getattr(root, "aggregated_output", None)),
        }
    if item_type in {"mcpToolCall", "dynamicToolCall", "collabAgentToolCall"}:
        tool = getattr(root, "tool", item_type)
        server = getattr(root, "server", None) or getattr(root, "namespace", None)
        result = getattr(root, "result", None) or getattr(root, "content_items", None)
        return "worker.tool", {
            **common,
            "tool": f"{server}.{tool}" if server else str(tool),
            "intent": intent,
            "arguments": _json_value(getattr(root, "arguments", None)),
            "result": _json_value(result),
            "error": _json_value(getattr(root, "error", None)),
            "duration_ms": getattr(root, "duration_ms", None),
        }
    if item_type == "fileChange":
        return "worker.file_change", {
            **common,
            "intent": intent,
            "changes": _json_value(getattr(root, "changes", [])),
        }
    if item_type == "webSearch":
        return "worker.tool", {
            **common,
            "tool": "web_search",
            "intent": intent,
            "query": getattr(root, "query", None),
            "action": _json_value(getattr(root, "action", None)),
        }
    return "worker.activity", common


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
        activity: Callable[..., Awaitable[None]] | None = None,
        heartbeat_interval_s: float = 15.0,
        require_solver_artifact: bool = True,
    ) -> None:
        if max_turns < 1:
            raise ValueError("max_turns must be positive")
        self._sdk_factory = sdk_factory
        self.max_turns = max_turns
        if heartbeat_interval_s <= 0:
            raise ValueError("heartbeat_interval_s must be positive")
        self._activity = activity
        self.heartbeat_interval_s = heartbeat_interval_s
        self.require_solver_artifact = require_solver_artifact

    async def _emit(self, assignment: WorkerAssignment, event_type: str, **payload: object) -> None:
        if self._activity is not None:
            await self._activity(
                event_type,
                run_id=assignment.run_id,
                worker_id=assignment.worker_id,
                challenge_id=assignment.challenge_id,
                challenge_title=assignment.challenge_title,
                challenge_category=assignment.challenge_category,
                worker_number=assignment.worker_number,
                **payload,
            )

    async def _run_turn(
        self, thread: Any, prompt: str, assignment: WorkerAssignment, turn_number: int
    ) -> str | None:
        if not hasattr(thread, "turn"):
            result = await thread.run(prompt, output_schema=REPORT_SCHEMA)
            return result.final_response

        handle = await thread.turn(
            prompt,
            output_schema=REPORT_SCHEMA,
            summary="detailed",
        )
        await self._emit(assignment, "worker.turn_started", turn=turn_number)
        stream = handle.stream().__aiter__()
        pending: asyncio.Task[Any] | None = None
        final_response: str | None = None
        completed_turn: Any = None
        current_intent: str | None = None
        started = time.monotonic()
        while True:
            if pending is None:
                pending = asyncio.create_task(anext(stream))
            done, _ = await asyncio.wait({pending}, timeout=self.heartbeat_interval_s)
            if not done:
                await self._emit(
                    assignment,
                    "worker.heartbeat",
                    turn=turn_number,
                    elapsed_s=round(time.monotonic() - started, 1),
                    summary=f"still running — turn {turn_number}",
                )
                continue
            try:
                notification = pending.result()
            except StopAsyncIteration:
                break
            finally:
                pending = None
            payload = getattr(notification, "payload", notification)
            item = getattr(payload, "item", None)
            root = getattr(item, "root", item)
            text = getattr(root, "text", None)
            phase = getattr(root, "phase", None)
            phase_value = getattr(phase, "value", phase)
            if (
                type(payload).__name__ == "ItemCompletedNotification"
                and isinstance(text, str)
                and phase_value in {None, "final_answer", "finalAnswer"}
            ):
                final_response = text
            item_type = getattr(root, "type", None)
            if item_type is not None:
                status = "completed" if "Completed" in type(payload).__name__ else "started"
                if str(item_type) == "reasoning" and status == "completed":
                    current_intent = _reasoning_summary(root) or current_intent
                event_type, activity = _item_activity(root, status, current_intent)
                await self._emit(
                    assignment,
                    event_type,
                    turn=turn_number,
                    **activity,
                )
            token_usage = getattr(payload, "token_usage", None)
            if token_usage is not None:
                usage = token_usage.model_dump(mode="json", by_alias=True) if hasattr(token_usage, "model_dump") else {}
                await self._emit(assignment, "worker.token_usage", turn=turn_number, usage=usage)
            if type(payload).__name__ == "TurnCompletedNotification":
                completed_turn = getattr(payload, "turn", None)
        if completed_turn is None:
            raise RuntimeError("Codex stream ended without turn/completed")
        turn_status = getattr(getattr(completed_turn, "status", None), "value", None)
        if turn_status != "completed":
            error = getattr(completed_turn, "error", None)
            message = getattr(error, "message", None)
            raise RuntimeError(message or f"Codex turn ended with status {turn_status or 'unknown'}")
        await self._emit(
            assignment,
            "worker.turn_completed",
            turn=turn_number,
            elapsed_s=round(time.monotonic() - started, 1),
        )
        return final_response

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

                for turn_number in range(1, self.max_turns + 1):
                    response = await self._run_turn(thread, prompt, assignment, turn_number)
                    worker_report = _report_from_response(assignment, response)
                    if (
                        self.require_solver_artifact
                        and worker_report.kind in {ReportKind.FLAG_CANDIDATE, ReportKind.COMPLETED}
                    ):
                        solver = find_solver(workspace, worker_report.artifacts)
                        if solver is None:
                            prompt = (
                                "Before you can finish, create a non-empty reusable solver.py (or "
                                "an equivalent solve/exploit source file) in the workspace. It must "
                                "reproduce the solution from a clean run. Then report again and include "
                                "the file path in artifacts."
                            )
                            continue
                        relative_solver = str(solver.relative_to(workspace))
                        if relative_solver not in worker_report.artifacts:
                            worker_report = WorkerReport(
                                worker_report.run_id,
                                worker_report.worker_id,
                                worker_report.challenge_id,
                                worker_report.kind,
                                worker_report.summary,
                                (*worker_report.artifacts, relative_solver),
                                worker_report.flag_candidate,
                            )
                    if (
                        worker_report.kind is ReportKind.COMPLETED
                        and worker_report.flag_candidate
                    ):
                        worker_report = WorkerReport(
                            worker_report.run_id,
                            worker_report.worker_id,
                            worker_report.challenge_id,
                            ReportKind.FLAG_CANDIDATE,
                            worker_report.summary,
                            worker_report.artifacts,
                            worker_report.flag_candidate,
                        )
                    decision = await report(worker_report)
                    if worker_report.kind in {ReportKind.COMPLETED, ReportKind.FAILED}:
                        return worker_report
                    if worker_report.kind is ReportKind.FLAG_CANDIDATE:
                        decision = decision or ReportDecision(terminal=True)
                        if decision.accepted or (decision.accepted is None and decision.terminal):
                            completed = WorkerReport(
                                assignment.run_id,
                                assignment.worker_id,
                                assignment.challenge_id,
                                ReportKind.COMPLETED,
                                decision.message or worker_report.summary,
                                worker_report.artifacts,
                                worker_report.flag_candidate,
                            )
                            await report(completed)
                            return completed
                        if decision.terminal:
                            failed = WorkerReport(
                                assignment.run_id,
                                assignment.worker_id,
                                assignment.challenge_id,
                                ReportKind.FAILED,
                                decision.message or "flag submission stopped",
                                worker_report.artifacts,
                                worker_report.flag_candidate,
                            )
                            await report(failed)
                            return failed
                        prompt = (
                            f"The platform rejected candidate {worker_report.flag_candidate!r}. "
                            "Continue analysis on this same thread, find a different flag, and report it."
                        )
                        continue
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
