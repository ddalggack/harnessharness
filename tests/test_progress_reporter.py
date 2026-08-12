import asyncio
import json
import tempfile
import unittest
from io import StringIO
from pathlib import Path

from ctf_harness.events import EventBus, ProgressReporter


class ProgressReporterTests(unittest.TestCase):
    def test_human_console_only_prints_assignment_and_accepted_solve(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as tmp:
                bus = EventBus()
                output = StringIO()
                reporter = ProgressReporter(bus, Path(tmp), "run-1", stream=output)
                await reporter.start()
                metadata = dict(worker_id="worker-1", worker_number=1, challenge_id="web-1", challenge_title="Login")
                await bus.publish("worker.started", **metadata)
                await bus.publish("worker.reasoning", **metadata, status="completed", summary="inspect auth")
                await bus.publish("submission.rejected", **metadata, summary="wrong")
                await bus.publish("submission.accepted", **metadata, summary="correct")
                await reporter.stop()

                self.assertEqual(
                    output.getvalue().splitlines(),
                    ["[1] worker handles Login", "[1] worker solved Login"],
                )

        asyncio.run(scenario())

    def test_persistence_failure_does_not_break_reporter_shutdown(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as tmp:
                bus = EventBus()
                reporter = ProgressReporter(bus, Path(tmp), "run-1", console_mode="quiet")

                def fail(_event):
                    raise OSError("disk full")

                reporter._record = fail
                await reporter.start()
                await bus.publish("run.started", run_id="run-1")
                await reporter.stop()
                self.assertEqual(reporter.error, "OSError: disk full")

        asyncio.run(scenario())

    def test_persists_jsonl_and_current_status(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as tmp:
                bus = EventBus()
                reporter = ProgressReporter(bus, Path(tmp), "run-1", console_mode="quiet")
                await reporter.start()
                await bus.publish("worker.started", worker_id="worker-1", challenge_id="web-1")
                await bus.publish(
                    "submission.accepted",
                    worker_id="worker-1",
                    challenge_id="web-1",
                    candidate="FLAG{ok}",
                )
                await reporter.stop()

                events = [
                    json.loads(line)
                    for line in (Path(tmp) / "run-1" / "events.jsonl").read_text().splitlines()
                ]
                status = json.loads((Path(tmp) / "run-1" / "status.json").read_text())
                self.assertEqual([item["type"] for item in events], ["worker.started", "submission.accepted"])
                self.assertEqual(status["workers"]["worker-1"]["state"], "accepted")
                self.assertEqual(status["workers"]["worker-1"]["submission_status"], "accepted")
                self.assertEqual(status["workers"]["worker-1"]["challenge_id"], "web-1")

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
