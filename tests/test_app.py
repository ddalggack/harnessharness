import asyncio
import tempfile
import unittest
from pathlib import Path

from ctf_harness.app import build_codex_harness
from ctf_harness.platforms import CTFdPlatformAdapter, MemoryPlatformAdapter


class HarnessWiringTests(unittest.TestCase):
    def test_codex_harness_is_wired_with_one_worker_model_and_max_three_swarms(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness = build_codex_harness(
                platform=MemoryPlatformAdapter([]),
                runs_root=Path(tmp),
                coordinator_model="gpt-5.4",
                worker_model="gpt-5.4-mini",
                max_swarms=3,
            )

        self.assertEqual(harness.scheduler.max_workers, 3)
        self.assertEqual(harness.workflow.worker_model, "gpt-5.4-mini")
        self.assertEqual(harness.coordinator.model, "gpt-5.4")

    def test_ctfd_adapter_is_an_explicit_unimplemented_boundary(self):
        async def scenario() -> None:
            adapter = CTFdPlatformAdapter("https://ctf.invalid")
            with self.assertRaisesRegex(NotImplementedError, "CTFd adapter"):
                await adapter.list_challenges()
            with self.assertRaisesRegex(NotImplementedError, "CTFd adapter"):
                await adapter.list_solved_challenge_ids()

        asyncio.run(scenario())
