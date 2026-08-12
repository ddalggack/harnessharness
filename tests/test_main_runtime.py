import asyncio
import tempfile
import unittest
from pathlib import Path

from ctf_harness.domain import Challenge, WorkerProfile
from ctf_harness.domain.models import WorkerRecord
from ctf_harness.events import EventBus
from ctf_harness.main_agent import MainAgentRuntime
from ctf_harness.protocol import ReportKind, WorkerReport
from ctf_harness.storage import MemoryRunRepository


class MainAgentRuntimeTests(unittest.TestCase):
    def test_assignment_is_built_from_challenge_data_without_an_llm(self):
        async def scenario() -> None:
            runtime = MainAgentRuntime(MemoryRunRepository(), EventBus())
            challenge = Challenge(
                id="pwn-1",
                title="baby-bof",
                category="pwn",
                description="Exploit the supplied ELF.",
                host="ctf.example",
                port=31337,
            )

            with tempfile.TemporaryDirectory() as tmp:
                assignment = await runtime.create_assignment(
                    "run-1",
                    "run-1-pwn-1",
                    challenge,
                    Path(tmp),
                    "gpt-5.4-mini",
                )

            self.assertEqual(assignment.challenge_id, "pwn-1")
            self.assertEqual(assignment.challenge_title, "baby-bof")
            self.assertEqual(assignment.challenge_category, "pwn")
            self.assertEqual(assignment.challenge_description, "Exploit the supplied ELF.")
            self.assertEqual(assignment.host, "ctf.example")
            self.assertEqual(assignment.port, 31337)
            self.assertEqual(assignment.model, "gpt-5.4-mini")

        asyncio.run(scenario())

    def test_main_records_failed_report_without_requesting_llm_feedback(self):
        async def scenario() -> None:
            repository = MemoryRunRepository()
            events = EventBus()
            runtime = MainAgentRuntime(repository, events)
            await runtime.register(
                WorkerRecord("worker-1", "pwn-1", WorkerProfile("pwn", "worker:pwn"))
            )
            report = WorkerReport(
                    "run-1",
                    "worker-1",
                    "pwn-1",
                    ReportKind.FAILED,
                    "No autonomous recovery path remains",
                    ("evidence.log",),
                    "FLAG{candidate}",
                )
            feedback = await runtime.handle_report(report)

            self.assertIsNone(feedback)
            self.assertEqual(repository.workers["worker-1"].status.value, "failed")
            self.assertEqual(repository.workers["worker-1"].reports, [report])
            self.assertEqual(repository.workers["worker-1"].reports[0].artifacts, ("evidence.log",))
            self.assertEqual(repository.workers["worker-1"].reports[0].flag_candidate, "FLAG{candidate}")
            reported = next(event for event in events.history if event.type == "worker.reported")
            self.assertEqual(reported.payload["artifacts"], ("evidence.log",))
            self.assertEqual(reported.payload["flag_candidate"], "FLAG{candidate}")
            self.assertNotIn("coordinator.feedback", [event.type for event in events.history])

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
