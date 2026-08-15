import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from ctf_harness.domain import Challenge, WorkerProfile, WorkerStatus
from ctf_harness.domain.models import WorkerRecord
from ctf_harness.events import Event
from ctf_harness.protocol import ReportKind, WorkerReport
from ctf_harness.writeups import CodexWriteupGenerator, SolvedDatabase


class FakeWriteupThread:
    def __init__(self) -> None:
        self.prompts = []

    async def run(self, prompt):
        self.prompts.append(prompt)
        return SimpleNamespace(final_response="# 풀이\n\n재현 가능한 내용")


class FakeWriteupCodex:
    def __init__(self) -> None:
        self.thread = FakeWriteupThread()
        self.start_calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def thread_start(self, **kwargs):
        self.start_calls.append(kwargs)
        return self.thread


class SolvedDatabaseTests(unittest.TestCase):
    def test_archives_solver_events_status_and_generates_writeup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runs_root = root / "runs"
            workspace = runs_root / "run-1" / "challenges" / "web-1"
            workspace.mkdir(parents=True)
            (workspace / "solver.py").write_text("print('FLAG{ok}')\n", encoding="utf-8")

            challenge = Challenge("web-1", "Login", "web", "bypass login")
            report = WorkerReport(
                "run-1",
                "worker-1",
                challenge.id,
                ReportKind.COMPLETED,
                "flag accepted",
                ("solver.py",),
                "FLAG{ok}",
            )
            record = WorkerRecord(
                "worker-1",
                challenge.id,
                WorkerProfile("web", "worker-web"),
                "gpt-5.5",
                WorkerStatus.COMPLETED,
                [report],
            )
            events = [
                Event(
                    "submission.accepted",
                    {
                        "worker_id": "worker-1",
                        "challenge_id": challenge.id,
                        "candidate": "FLAG{ok}",
                    },
                    "2026-08-14T00:00:00+00:00",
                )
            ]
            database = SolvedDatabase(root / "solved-db")

            self.assertEqual(
                database.archive_run("run-1", [challenge], [record], events, runs_root),
                ["web-web-1"],
            )
            entry = database.entry("web-web-1")
            self.assertTrue((entry / "solver.py").is_file())
            self.assertEqual(json.loads((entry / "event.json").read_text())[0]["type"], "submission.accepted")
            self.assertEqual(json.loads((entry / "status.json").read_text())["verification"], "accepted")

            client = FakeWriteupCodex()
            output = asyncio.run(
                CodexWriteupGenerator(lambda: client).generate(entry, "gpt-5.5")
            )
            self.assertEqual(output.read_text(encoding="utf-8"), "# 풀이\n\n재현 가능한 내용\n")
            self.assertEqual(client.start_calls[0]["cwd"], str(entry))
            self.assertIn("independent", client.start_calls[0]["developer_instructions"])

    def test_dashboard_controller_generates_and_exposes_writeup_download(self):
        from ctf_harness.dashboard import DashboardController

        class Generator:
            async def generate(self, entry, model):
                self.model = model
                output = entry / "write-up.md"
                output.write_text("# generated\n", encoding="utf-8")
                return output

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            generator = Generator()
            controller = DashboardController(root / "runs", writeup_generator=generator)
            try:
                entry = root / "solved-db" / "web-one"
                entry.mkdir(parents=True)
                (entry / "solver.py").write_text("print('ok')\n", encoding="utf-8")
                (entry / "event.json").write_text("[]\n", encoding="utf-8")
                (entry / "status.json").write_text(json.dumps({
                    "record_id": "web-one",
                    "challenge": {"id": "one", "title": "One", "category": "web"},
                    "model": "gpt-5.5",
                    "solver_file": "solver.py",
                    "writeup": {"status": "not-generated"},
                }))

                state = controller.generate_writeup("web-one")
                self.assertTrue(state["solvedDb"][0]["hasWriteup"])
                self.assertEqual(state["solvedDb"][0]["writeupStatus"], "completed")
                self.assertEqual(generator.model, "gpt-5.5")
                self.assertEqual(controller.solved_file("web-one", "write-up.md").name, "write-up.md")
                self.assertEqual(
                    controller.writeup_preview("web-one"),
                    {"recordId": "web-one", "content": "# generated\n"},
                )

                state = controller.delete_solved_record("web-one")
                self.assertEqual(state["solvedDb"], [])
                self.assertFalse(entry.exists())

                entry.mkdir(parents=True)
                (entry / "status.json").write_text(json.dumps({
                    "record_id": "web-one",
                    "challenge": {"id": "one", "title": "One", "category": "web"},
                    "writeup": {"status": "not-generated"},
                }))
                controller.reset()
                self.assertFalse((root / "solved-db").exists())
            finally:
                controller.shutdown()


if __name__ == "__main__":
    unittest.main()
