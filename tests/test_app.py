import asyncio
import tempfile
import unittest
from pathlib import Path

from ctf_harness.app import build_codex_harness
from ctf_harness.platforms import CTFdPlatformAdapter, MemoryPlatformAdapter


class HarnessWiringTests(unittest.TestCase):
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
