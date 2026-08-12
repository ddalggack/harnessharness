import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from ctf_harness.domain import Challenge
from ctf_harness.main_agent.codex import CodexCoordinatorBackend
from ctf_harness.protocol import Feedback, ReportKind, WorkerAssignment, WorkerReport
from ctf_harness.worker.codex import REPORT_SCHEMA, CodexWorkerRunner


class FakeThread:
    def __init__(self, responses: list[dict[str, object]]) -> None:
        self.id = "thread-1"
        self.responses = list(responses)
        self.prompts: list[str] = []

    async def run(self, prompt: str, **kwargs):
        self.prompts.append(prompt)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return SimpleNamespace(final_response=json.dumps(response))


class FakeCodex:
    def __init__(self, thread: FakeThread) -> None:
        self.thread = thread
        self.start_calls: list[dict[str, object]] = []
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self.closed = True

    async def thread_start(self, **kwargs):
        self.start_calls.append(kwargs)
        return self.thread


class CodexRuntimeTests(unittest.TestCase):
    def test_worker_output_schema_requires_every_declared_property(self):
        self.assertEqual(
            set(REPORT_SCHEMA["properties"]),
            set(REPORT_SCHEMA["required"]),
        )

    def test_coordinator_keeps_one_thread_for_assignments_and_feedback(self):
        async def scenario() -> None:
            thread = FakeThread(
                [
                    {"objective": "Analyze the binary and produce a reproducible exploit.", "profile": "pwn"},
                    {"directive": "Compare the provided loader and libc before continuing."},
                ]
            )
            client = FakeCodex(thread)
            coordinator = CodexCoordinatorBackend(
                model="gpt-5.4",
                cwd=Path("."),
                sdk_factory=lambda: client,
            )

            await coordinator.start()
            assignment = await coordinator.create_assignment(
                run_id="run-1",
                worker_id="run-1-pwn-1",
                challenge=Challenge("pwn-1", "baby-bof", "pwn", "stack challenge"),
                workspace=Path("workspace"),
                worker_model="gpt-5.4-mini",
            )
            feedback = await coordinator.review_report(
                WorkerReport(
                    "run-1",
                    "run-1-pwn-1",
                    "pwn-1",
                    ReportKind.BLOCKED,
                    "libc mismatch",
                )
            )
            await coordinator.stop()

            self.assertEqual(len(client.start_calls), 1)
            self.assertEqual(assignment.model, "gpt-5.4-mini")
            self.assertEqual(assignment.profile, "pwn")
            self.assertEqual(
                feedback,
                Feedback("run-1", "run-1-pwn-1", "Compare the provided loader and libc before continuing."),
            )
            self.assertEqual(len(thread.prompts), 2)
            self.assertTrue(client.closed)

        asyncio.run(scenario())

    def test_coordinator_serializes_turns_on_its_long_lived_thread(self):
        async def scenario() -> None:
            class SlowThread(FakeThread):
                def __init__(self):
                    super().__init__([
                        {"objective": "solve one", "profile": "pwn"},
                        {"objective": "solve two", "profile": "rev"},
                    ])
                    self.running = 0
                    self.peak = 0

                async def run(self, prompt: str, **kwargs):
                    self.running += 1
                    self.peak = max(self.peak, self.running)
                    await asyncio.sleep(0.01)
                    try:
                        return await super().run(prompt, **kwargs)
                    finally:
                        self.running -= 1

            thread = SlowThread()
            coordinator = CodexCoordinatorBackend("gpt-5.4", Path("."), lambda: FakeCodex(thread))
            await coordinator.start()
            await asyncio.gather(
                coordinator.create_assignment("run", "w1", Challenge("1", "One", "pwn"), Path("one"), "gpt-5.4"),
                coordinator.create_assignment("run", "w2", Challenge("2", "Two", "rev"), Path("two"), "gpt-5.4"),
            )
            await coordinator.stop()
            self.assertEqual(thread.peak, 1)

        asyncio.run(scenario())

    def test_worker_uses_one_model_and_continues_after_coordinator_feedback(self):
        async def scenario() -> None:
            thread = FakeThread(
                [
                    {"kind": "blocked", "summary": "Need loader guidance", "artifacts": []},
                    {
                        "kind": "completed",
                        "summary": "Exploit reproduced",
                        "artifacts": ["exploit.py", "REPORT.md"],
                        "flag_candidate": "FLAG{demo}",
                    },
                ]
            )
            client = FakeCodex(thread)
            runner = CodexWorkerRunner(sdk_factory=lambda: client, max_turns=3)
            seen: list[WorkerReport] = []

            async def report(item: WorkerReport):
                seen.append(item)
                if item.kind is ReportKind.BLOCKED:
                    return Feedback(item.run_id, item.worker_id, "Inspect ld.so and libc hashes.")
                return None

            with tempfile.TemporaryDirectory() as tmp:
                assignment = WorkerAssignment(
                    "run-1",
                    "worker-1",
                    "pwn-1",
                    "Solve the challenge",
                    tmp,
                    "pwn",
                    "gpt-5.4-mini",
                )
                result = await runner.run(assignment, report)

            self.assertEqual([item.kind for item in seen], [ReportKind.BLOCKED, ReportKind.COMPLETED])
            self.assertEqual(result.flag_candidate, "FLAG{demo}")
            self.assertEqual(client.start_calls[0]["model"], "gpt-5.4-mini")
            self.assertEqual(len(client.start_calls), 1)
            self.assertIn("Inspect ld.so", thread.prompts[1])
            self.assertTrue(client.closed)

        asyncio.run(scenario())

    def test_worker_runtime_error_is_reported_as_failed(self):
        async def scenario() -> None:
            client = FakeCodex(FakeThread([RuntimeError("app-server unavailable")]))
            runner = CodexWorkerRunner(sdk_factory=lambda: client)
            seen: list[WorkerReport] = []

            async def report(item: WorkerReport):
                seen.append(item)
                return None

            with tempfile.TemporaryDirectory() as tmp:
                result = await runner.run(
                    WorkerAssignment("run", "worker", "pwn", "solve", tmp, "pwn", "gpt-5.4"),
                    report,
                )

            self.assertEqual(result.kind, ReportKind.FAILED)
            self.assertIn("app-server unavailable", result.summary)
            self.assertEqual(seen, [result])
            self.assertTrue(client.closed)

        asyncio.run(scenario())
