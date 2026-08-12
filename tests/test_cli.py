import asyncio
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from ctf_harness.cli import _codex_demo, _ctfd_run
from ctf_harness.domain import WorkerProfile
from ctf_harness.domain.models import WorkerRecord, WorkerStatus
from ctf_harness.protocol import ReportKind, WorkerReport
from ctf_harness.storage import MemoryRunRepository
from ctf_harness.workflows import CtfRunResult


class CodexDemoCliTests(unittest.TestCase):
    def test_codex_demo_runs_real_harness_boundary_and_prints_reports(self):
        async def scenario() -> None:
            repository = MemoryRunRepository()
            report = WorkerReport(
                "codex-demo",
                "codex-demo-demo-1",
                "demo-1",
                ReportKind.COMPLETED,
                "Codex API call completed",
                ("notes.md",),
            )
            repository.workers[report.worker_id] = WorkerRecord(
                report.worker_id,
                report.challenge_id,
                WorkerProfile("misc", "worker:misc"),
                status=WorkerStatus.COMPLETED,
                reports=[report],
            )

            class Workflow:
                async def run(self, run_id):
                    self.run_id = run_id
                    return CtfRunResult(run_id, 1, 1, 3)

            class Harness:
                workflow = Workflow()

                def __init__(self):
                    self.repository = repository

            captured = {}

            def builder(**kwargs):
                captured.update(kwargs)
                return Harness()

            with tempfile.TemporaryDirectory() as tmp:
                output = StringIO()
                with redirect_stdout(output):
                    exit_code = await _codex_demo(
                        model="gpt-5.4",
                        runs_root=Path(tmp),
                        build_harness=builder,
                    )

            payload = json.loads(output.getvalue())
            self.assertEqual(exit_code, 0)
            self.assertEqual(payload["completed_workers"], 1)
            self.assertEqual(payload["workers"][0]["status"], "completed")
            self.assertEqual(payload["workers"][0]["reports"][0]["artifacts"], ["notes.md"])
            self.assertEqual(captured["worker_model"], "gpt-5.4")
            self.assertEqual(captured["max_swarms"], 1)

        asyncio.run(scenario())

    def test_ctfd_run_builds_harness_with_real_adapter_boundary(self):
        async def scenario() -> None:
            repository = MemoryRunRepository()

            class Workflow:
                async def run(self, run_id):
                    self.run_id = run_id
                    return CtfRunResult(run_id, 0, 0, 1)

            class Harness:
                workflow = Workflow()
                scheduler = type("Scheduler", (), {"tasks": {}})()

                def __init__(self):
                    self.repository = repository

            captured = {}

            class Adapter:
                def __init__(self, base_url, token=None, timeout_s=30.0):
                    captured.update(base_url=base_url, token=token, timeout_s=timeout_s)

            def builder(**kwargs):
                captured.update(kwargs)
                return Harness()

            output = StringIO()
            with redirect_stdout(output):
                exit_code = await _ctfd_run(
                    base_url="https://ctf.example",
                    token="token",
                    model="gpt-5.4",
                    runs_root=Path("runs"),
                    max_swarms=2,
                    run_id="event-1",
                    timeout_s=10.0,
                    adapter_factory=Adapter,
                    build_harness=builder,
                )

            payload = json.loads(output.getvalue())
            self.assertEqual(exit_code, 0)
            self.assertEqual(captured["base_url"], "https://ctf.example")
            self.assertEqual(captured["token"], "token")
            self.assertEqual(captured["worker_model"], "gpt-5.4")
            self.assertEqual(captured["max_swarms"], 2)
            self.assertFalse(captured["submit_flags"])
            self.assertEqual(captured["max_wrong_submissions"], 3)
            self.assertEqual(captured["heartbeat_interval_s"], 15.0)
            self.assertEqual(payload["run_id"], "event-1")

        asyncio.run(scenario())

    def test_ctfd_run_reports_platform_failure_as_json(self):
        async def scenario() -> None:
            class Workflow:
                async def run(self, run_id):
                    raise RuntimeError("CTFd unavailable")

            class Harness:
                workflow = Workflow()

            class Adapter:
                def __init__(self, base_url, token=None, timeout_s=30.0):
                    pass

            output = StringIO()
            with redirect_stdout(output):
                exit_code = await _ctfd_run(
                    base_url="https://ctf.example",
                    token=None,
                    model="gpt-5.4",
                    runs_root=Path("runs"),
                    max_swarms=1,
                    run_id="event-1",
                    timeout_s=10.0,
                    adapter_factory=Adapter,
                    build_harness=lambda **kwargs: Harness(),
                )

            payload = json.loads(output.getvalue())
            self.assertEqual(exit_code, 1)
            self.assertEqual(payload["run_id"], "event-1")
            self.assertEqual(payload["error"], "RuntimeError: CTFd unavailable")

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
