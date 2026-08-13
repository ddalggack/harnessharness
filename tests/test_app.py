import asyncio
import tempfile
import unittest
from pathlib import Path

from ctf_harness.app import CodexHarness, build_codex_harness
from ctf_harness.domain import Challenge
from ctf_harness.events import EventBus
from ctf_harness.main_agent import MainAgentRuntime
from ctf_harness.platforms import CTFdPlatformAdapter, MemoryPlatformAdapter
from ctf_harness.scheduler import LocalWorkerScheduler
from ctf_harness.storage import LocalObjectStore, MemoryRunRepository
from ctf_harness.worker import DemoWorkerRunner
from ctf_harness.workflows import CtfRunWorkflow


class HarnessWiringTests(unittest.TestCase):
    def test_dashboard_uses_harneharness_thumbprint_theme_and_bundled_noto_font(self):
        from ctf_harness.dashboard import _CONTENT_TYPES

        root = Path(__file__).resolve().parents[1]
        console = root / "src/ctf_harness/console"
        html = (console / "index.html").read_text(encoding="utf-8")
        css = (console / "styles.css").read_text(encoding="utf-8")

        self.assertIn("<title>HarneHarness</title>", html)
        self.assertIn("<h1>HarneHarness</h1>", html)
        self.assertIn("--primary: #5055b1", css)
        self.assertIn("--accent: #474c98", css)
        self.assertIn("--canvas: #ffffff", css)
        self.assertIn("--line: #d5d5d5", css)
        self.assertIn('font-family: "Noto Sans KR"', css)
        self.assertIn('url("/assets/fonts/NotoSansKR-Variable.ttf")', css)
        self.assertIn("border-radius: 4px", css)
        self.assertGreater(
            (console / "assets/fonts/NotoSansKR-Variable.ttf").stat().st_size,
            10_000_000,
        )
        self.assertEqual(_CONTENT_TYPES[".ttf"], "font/ttf")

    def test_dashboard_exposes_flag_auto_submit_switch(self):
        root = Path(__file__).resolve().parents[1]
        html = (root / "src/ctf_harness/console/index.html").read_text(encoding="utf-8")
        javascript = (root / "src/ctf_harness/console/app.js").read_text(encoding="utf-8")

        self.assertIn('id="auto-submit-flags"', html)
        self.assertIn("플래그 자동 제출", html)
        self.assertLess(html.index('id="auto-submit-flags"'), html.index('id="connect-button"'))
        self.assertIn("autoSubmitFlags", javascript)

    def test_dashboard_omits_execution_log_panel_and_event_payload(self):
        from ctf_harness.dashboard import DashboardController

        root = Path(__file__).resolve().parents[1]
        console = root / "src/ctf_harness/console"
        html = (console / "index.html").read_text(encoding="utf-8")
        javascript = (console / "app.js").read_text(encoding="utf-8")

        self.assertNotIn("실행 기록", html)
        self.assertNotIn('id="event-log"', html)
        self.assertNotIn("renderEvents", javascript)

        with tempfile.TemporaryDirectory() as tmp:
            controller = DashboardController(Path(tmp))
            try:
                self.assertNotIn("events", controller.public_state())
            finally:
                controller.shutdown()

    def test_dashboard_exposes_incremental_worker_event_stream(self):
        from ctf_harness.dashboard import DashboardController

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            controller = DashboardController(root)
            try:
                platform = MemoryPlatformAdapter([])
                repository = MemoryRunRepository()
                events = EventBus()
                scheduler = LocalWorkerScheduler(DemoWorkerRunner, max_workers=1)
                workflow = CtfRunWorkflow(
                    platform,
                    scheduler,
                    repository,
                    LocalObjectStore(root),
                    events,
                    main=MainAgentRuntime(repository, events),
                )
                controller.harness = CodexHarness(workflow, scheduler, repository, events)
                controller.run_id = "run-events"
                controller._submit(events.publish(
                    "worker.heartbeat",
                    run_id="run-events",
                    worker_id="run-events-one",
                    worker_number=1,
                    summary="still working",
                ))

                first = controller.event_batch(0)
                self.assertEqual(first["runId"], "run-events")
                self.assertEqual(first["events"][0]["type"], "worker.heartbeat")
                self.assertEqual(first["events"][0]["payload"]["worker_number"], 1)
                self.assertEqual(first["nextCursor"], 1)
                self.assertEqual(controller.event_batch(first["nextCursor"])["events"], [])

                javascript = (
                    Path(__file__).resolve().parents[1] / "src/ctf_harness/console/app.js"
                ).read_text(encoding="utf-8")
                self.assertIn('/api/events?cursor=', javascript)
                self.assertIn('case "worker.token_usage"', javascript)
                self.assertIn('case "worker.tool"', javascript)
            finally:
                controller.shutdown()

    def test_dashboard_tracks_reused_slot_done_count_current_problem_and_tokens(self):
        from ctf_harness.dashboard import DashboardController

        challenges = [
            Challenge("one", "First Problem", "web", "first"),
            Challenge("two", "Second Problem", "web", "second"),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            controller = DashboardController(root)
            try:
                platform = MemoryPlatformAdapter(challenges)
                repository = MemoryRunRepository()
                events = EventBus()
                scheduler = LocalWorkerScheduler(DemoWorkerRunner, max_workers=1)
                workflow = CtfRunWorkflow(
                    platform,
                    scheduler,
                    repository,
                    LocalObjectStore(root),
                    events,
                    main=MainAgentRuntime(repository, events),
                    worker_model="gpt-5.4-mini",
                )
                controller.platform = platform
                controller.challenges = challenges
                controller.worker_limit = 1
                controller.run_id = "run-slots"
                controller.harness = CodexHarness(workflow, scheduler, repository, events)
                controller._submit(workflow.run(controller.run_id))
                controller._submit(events.publish(
                    "worker.token_usage",
                    run_id=controller.run_id,
                    worker_id="run-slots-two",
                    worker_number=1,
                    usage={
                        "total": {
                            "totalTokens": 1234,
                            "inputTokens": 900,
                            "cachedInputTokens": 100,
                            "outputTokens": 334,
                            "reasoningOutputTokens": 50,
                        },
                        "modelContextWindow": 200000,
                    },
                ))

                state = controller.public_state()
                worker = state["workers"][0]
                self.assertEqual(worker["completed"], 2)
                self.assertEqual(worker["challengeName"], "Second Problem")
                self.assertEqual(worker["status"], "completed")
                self.assertEqual(worker["model"], "gpt-5.4-mini")
                self.assertEqual(worker["tokenUsage"]["totalTokens"], 1234)
                self.assertEqual(worker["tokenUsage"]["modelContextWindow"], 200000)
                self.assertIsNotNone(worker["startedAt"])
                self.assertIsNotNone(worker["finishedAt"])
                second = next(item for item in state["challenges"] if item["id"] == "two")
                self.assertEqual(second["workerId"], "Worker 1")
                self.assertEqual(second["backendWorkerId"], "run-slots-two")
            finally:
                controller.shutdown()

    def test_dashboard_only_enables_auto_submit_for_ctfd_runs(self):
        from ctf_harness.dashboard import DashboardController

        with tempfile.TemporaryDirectory() as tmp:
            controller = DashboardController(Path(tmp))
            try:
                controller.mode = "demo"
                controller.platform = MemoryPlatformAdapter([])
                controller.start({"autoSubmitFlags": True})
                self.assertFalse(controller.auto_submit_flags)
                self.assertIsNone(controller.harness.workflow.main.submission_broker)
                controller.run_future.result(timeout=5)

                controller.mode = "ctfd"
                controller.start({"autoSubmitFlags": True})
                self.assertTrue(controller.auto_submit_flags)
                self.assertIsNotNone(controller.harness.workflow.main.submission_broker)
                controller.run_future.result(timeout=5)
            finally:
                controller.shutdown()

    def test_harness_uses_code_coordinator_one_worker_model_and_max_three_swarms(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness = build_codex_harness(
                platform=MemoryPlatformAdapter([]),
                runs_root=Path(tmp),
                worker_model="gpt-5.4-mini",
                max_swarms=3,
            )

        self.assertEqual(harness.scheduler.max_workers, 3)
        self.assertEqual(harness.workflow.worker_model, "gpt-5.4-mini")
        self.assertFalse(hasattr(harness, "coordinator"))

    def test_ctfd_adapter_validates_base_url(self):
        with self.assertRaisesRegex(ValueError, "base_url"):
            CTFdPlatformAdapter("ctf.invalid")
