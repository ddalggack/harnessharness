import asyncio
import tempfile
import unittest
from pathlib import Path

from ctf_harness.app import build_codex_harness
from ctf_harness.platforms import CTFdPlatformAdapter, MemoryPlatformAdapter


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
