import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from ctf_harness.protocol import ReportKind, WorkerAssignment, WorkerReport
from ctf_harness.worker.codex import REPORT_SCHEMA, CodexWorkerRunner, _item_activity
from ctf_harness.worker.runner import ReportDecision


class FakeThread:
    def __init__(self, responses: list[dict[str, object] | Exception]) -> None:
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


class StreamingThread:
    id = "thread-stream"

    def __init__(self, response, delay=0.0):
        self.response = response
        self.delay = delay
        self.prompts = []
        self.turn_kwargs = []

    async def turn(self, prompt, **kwargs):
        self.prompts.append(prompt)
        self.turn_kwargs.append(kwargs)
        response = self.response
        delay = self.delay

        class Handle:
            async def stream(self):
                if delay:
                    await asyncio.sleep(delay)
                ItemCompletedNotification = type("ItemCompletedNotification", (), {})
                item_completed = ItemCompletedNotification()
                item = SimpleNamespace(
                    root=SimpleNamespace(
                        type="agentMessage",
                        text=json.dumps(response),
                        phase=None,
                    )
                )
                item_completed.item = item
                yield SimpleNamespace(payload=item_completed)
                TurnCompletedNotification = type("TurnCompletedNotification", (), {})
                turn_completed = TurnCompletedNotification()
                turn_completed.turn = SimpleNamespace(
                    status=SimpleNamespace(value="completed"), error=None
                )
                yield SimpleNamespace(payload=turn_completed)

        return Handle()


def assignment(workspace: str, model: str = "gpt-5.4-mini") -> WorkerAssignment:
    return WorkerAssignment(
        run_id="run-1",
        worker_id="worker-1",
        challenge_id="pwn-1",
        challenge_title="baby-bof",
        challenge_category="pwn",
        challenge_description="Exploit the supplied ELF and recover the flag.",
        workspace_uri=workspace,
        model=model,
        host="ctf.example",
        port=31337,
    )


class CodexRuntimeTests(unittest.TestCase):
    def test_command_activity_includes_command_output_and_reasoning_intent(self):
        event_type, payload = _item_activity(
            SimpleNamespace(
                type="commandExecution",
                command="checksec ./chall",
                cwd="/workspace",
                exit_code=0,
                aggregated_output="NX enabled",
            ),
            "completed",
            "inspect binary protections",
        )
        self.assertEqual(event_type, "worker.tool")
        self.assertEqual(payload["tool"], "shell")
        self.assertEqual(payload["intent"], "inspect binary protections")
        self.assertEqual(payload["command"], "checksec ./chall")
        self.assertEqual(payload["output"], "NX enabled")

    def test_worker_report_states_exclude_blocked(self):
        self.assertEqual(
            {kind.value for kind in ReportKind},
            {"checkpoint", "flag_candidate", "completed", "failed"},
        )

    def test_worker_output_schema_requires_every_declared_property(self):
        self.assertEqual(
            set(REPORT_SCHEMA["properties"]),
            set(REPORT_SCHEMA["required"]),
        )

    def test_worker_must_leave_reusable_solver_before_completion(self):
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as tmp:
                workspace = Path(tmp)

                class SolverThread(FakeThread):
                    async def run(self, prompt: str, **kwargs):
                        self.prompts.append(prompt)
                        if len(self.prompts) == 2:
                            (workspace / "solver.py").write_text("print('FLAG{ok}')\n")
                        return SimpleNamespace(final_response=json.dumps({
                            "kind": "completed",
                            "summary": "done",
                            "artifacts": [],
                            "flag_candidate": None,
                        }))

                thread = SolverThread([])
                runner = CodexWorkerRunner(
                    sdk_factory=lambda: FakeCodex(thread),
                    max_turns=2,
                )
                seen = []

                async def report(item):
                    seen.append(item)
                    return None

                result = await runner.run(assignment(tmp), report)
                self.assertEqual(len(thread.prompts), 2)
                self.assertIn("solver.py", thread.prompts[1])
                self.assertEqual(result.artifacts, ("solver.py",))
                self.assertEqual(seen, [result])

        asyncio.run(scenario())

    def test_worker_receives_challenge_data_and_plans_on_its_own_thread(self):
        async def scenario() -> None:
            thread = FakeThread(
                [
                    {
                        "kind": "checkpoint",
                        "summary": "Created an analysis plan",
                        "artifacts": [],
                        "flag_candidate": None,
                    },
                    {
                        "kind": "completed",
                        "summary": "Exploit reproduced",
                        "artifacts": ["exploit.py", "REPORT.md"],
                        "flag_candidate": "FLAG{demo}",
                    },
                ]
            )
            client = FakeCodex(thread)
            runner = CodexWorkerRunner(
                sdk_factory=lambda: client,
                max_turns=3,
                require_solver_artifact=False,
            )
            seen: list[WorkerReport] = []

            async def report(item: WorkerReport):
                seen.append(item)
                return None

            with tempfile.TemporaryDirectory() as tmp:
                result = await runner.run(assignment(tmp), report)

            self.assertEqual(
                [item.kind for item in seen],
                [ReportKind.CHECKPOINT, ReportKind.FLAG_CANDIDATE, ReportKind.COMPLETED],
            )
            self.assertEqual(result.flag_candidate, "FLAG{demo}")
            self.assertEqual(client.start_calls[0]["model"], "gpt-5.4-mini")
            self.assertEqual(len(client.start_calls), 1)
            self.assertIn("baby-bof", thread.prompts[0])
            self.assertIn("pwn", thread.prompts[0])
            self.assertIn("Exploit the supplied ELF", thread.prompts[0])
            self.assertIn("ctf.example:31337", thread.prompts[0])
            self.assertIn("decide your own analysis and solving strategy", thread.prompts[0])
            self.assertEqual(
                thread.prompts[1],
                "Continue autonomously and return the next meaningful report.",
            )
            self.assertTrue(client.closed)

        asyncio.run(scenario())

    def test_rejected_candidate_continues_on_same_thread_until_accepted(self):
        async def scenario() -> None:
            thread = FakeThread(
                [
                    {"kind": "flag_candidate", "summary": "first", "artifacts": [], "flag_candidate": "FLAG{bad}"},
                    {"kind": "flag_candidate", "summary": "second", "artifacts": [], "flag_candidate": "FLAG{ok}"},
                ]
            )
            runner = CodexWorkerRunner(
                sdk_factory=lambda: FakeCodex(thread),
                max_turns=3,
                require_solver_artifact=False,
            )
            seen = []

            async def report(item):
                seen.append(item)
                if item.kind is ReportKind.FLAG_CANDIDATE:
                    return ReportDecision(accepted=item.flag_candidate == "FLAG{ok}")
                return None

            with tempfile.TemporaryDirectory() as tmp:
                result = await runner.run(assignment(tmp), report)

            self.assertEqual(result.kind, ReportKind.COMPLETED)
            self.assertEqual(result.flag_candidate, "FLAG{ok}")
            self.assertEqual(len(thread.prompts), 2)
            self.assertIn("rejected", thread.prompts[1].lower())
            self.assertEqual(seen[-1].kind, ReportKind.COMPLETED)

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
                result = await runner.run(assignment(tmp, "gpt-5.4"), report)

            self.assertEqual(result.kind, ReportKind.FAILED)
            self.assertIn("app-server unavailable", result.summary)
            self.assertEqual(seen, [result])
            self.assertTrue(client.closed)

        asyncio.run(scenario())

    def test_streaming_turn_emits_heartbeat_and_activity(self):
        async def scenario() -> None:
            thread = StreamingThread(
                {"kind": "completed", "summary": "done", "artifacts": [], "flag_candidate": None},
                delay=0.03,
            )
            activity = []

            async def emit(event_type, **payload):
                activity.append((event_type, payload))

            runner = CodexWorkerRunner(
                sdk_factory=lambda: FakeCodex(thread),
                heartbeat_interval_s=0.01,
                activity=emit,
                require_solver_artifact=False,
            )
            with tempfile.TemporaryDirectory() as tmp:
                result = await runner.run(assignment(tmp), lambda item: asyncio.sleep(0))

            self.assertEqual(result.kind, ReportKind.COMPLETED)
            event_types = [event_type for event_type, _ in activity]
            self.assertIn("worker.turn_started", event_types)
            self.assertIn("worker.heartbeat", event_types)
            self.assertIn("worker.message", event_types)
            self.assertIn("worker.turn_completed", event_types)
            self.assertEqual(thread.turn_kwargs[0]["summary"], "detailed")

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
