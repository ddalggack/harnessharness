import asyncio
import json
import tempfile
import unittest
from types import SimpleNamespace

from ctf_harness.protocol import ReportKind, WorkerAssignment, WorkerReport
from ctf_harness.worker.codex import REPORT_SCHEMA, CodexWorkerRunner


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
    def test_worker_report_states_exclude_blocked(self):
        self.assertEqual(
            {kind.value for kind in ReportKind},
            {"checkpoint", "completed", "failed"},
        )

    def test_worker_output_schema_requires_every_declared_property(self):
        self.assertEqual(
            set(REPORT_SCHEMA["properties"]),
            set(REPORT_SCHEMA["required"]),
        )

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
            runner = CodexWorkerRunner(sdk_factory=lambda: client, max_turns=3)
            seen: list[WorkerReport] = []

            async def report(item: WorkerReport):
                seen.append(item)
                return None

            with tempfile.TemporaryDirectory() as tmp:
                result = await runner.run(assignment(tmp), report)

            self.assertEqual(
                [item.kind for item in seen],
                [ReportKind.CHECKPOINT, ReportKind.COMPLETED],
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


if __name__ == "__main__":
    unittest.main()
