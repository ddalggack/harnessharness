import json
import tempfile
import unittest
from io import StringIO
from pathlib import Path

from ctf_harness.events import format_worker_event, seek_worker


class SeekWorkerTests(unittest.TestCase):
    def test_formats_tool_with_intent_and_output(self):
        started = format_worker_event(
            {
                "type": "worker.tool",
                "occurred_at": "2026-01-01T04:52:12+00:00",
                "payload": {
                    "tool": "shell",
                    "status": "started",
                    "intent": "inspect binary protections",
                    "command": "checksec ./chall",
                },
            }
        )
        completed = format_worker_event(
            {
                "type": "worker.tool",
                "occurred_at": "2026-01-01T04:52:13+00:00",
                "payload": {
                    "tool": "shell",
                    "status": "completed",
                    "intent": "inspect binary protections",
                    "command": "checksec ./chall",
                    "exit_code": 0,
                    "output": "NX enabled",
                },
            }
        )
        self.assertIn("• shell", started)
        self.assertIn("inspect binary protections", started)
        self.assertIn("checksec ./chall", started)
        self.assertIn("✓ shell · exit 0", completed)
        self.assertIn("│ NX enabled", completed)

    def test_collapses_skill_instruction_output(self):
        rendered = format_worker_event(
            {
                "type": "worker.tool",
                "occurred_at": "2026-01-01T04:52:13+00:00",
                "payload": {
                    "tool": "shell",
                    "status": "completed",
                    "command": "sed -n '1,240p' /skills/ctf-pwn/SKILL.md",
                    "exit_code": 0,
                    "output": "\n".join(f"line {index}" for index in range(100)),
                },
            }
        )
        self.assertIn("loaded skill instructions · 100 lines", rendered)
        self.assertNotIn("line 99", rendered)

    def test_seek_filters_worker_number(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "C001"
            run_dir.mkdir()
            events = [
                {"type": "worker.reasoning", "occurred_at": "2026-01-01T00:00:00+00:00", "payload": {"worker_number": 1, "status": "completed", "summary": "worker one"}},
                {"type": "worker.reasoning", "occurred_at": "2026-01-01T00:00:01+00:00", "payload": {"worker_number": 2, "status": "completed", "summary": "worker two"}},
            ]
            (run_dir / "events.jsonl").write_text(
                "".join(json.dumps(event) + "\n" for event in events), encoding="utf-8"
            )
            output = StringIO()
            result = seek_worker(Path(tmp), 2, run_id="C001", follow=False, raw=False, stream=output)
            self.assertEqual(result, 0)
            self.assertIn("worker two", output.getvalue())
            self.assertNotIn("worker one", output.getvalue())

    def test_seek_reused_slot_replays_only_latest_worker(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "C001"
            run_dir.mkdir()
            events = [
                {"type": "worker.started", "occurred_at": "2026-01-01T00:00:00+00:00", "payload": {"worker_number": 1, "worker_id": "old", "challenge_title": "Old"}},
                {"type": "worker.reasoning", "occurred_at": "2026-01-01T00:00:01+00:00", "payload": {"worker_number": 1, "worker_id": "old", "status": "completed", "summary": "old work"}},
                {"type": "worker.started", "occurred_at": "2026-01-01T00:00:02+00:00", "payload": {"worker_number": 1, "worker_id": "new", "challenge_title": "New"}},
                {"type": "worker.reasoning", "occurred_at": "2026-01-01T00:00:03+00:00", "payload": {"worker_number": 1, "worker_id": "new", "status": "completed", "summary": "new work"}},
            ]
            (run_dir / "events.jsonl").write_text(
                "".join(json.dumps(event) + "\n" for event in events), encoding="utf-8"
            )
            output = StringIO()
            seek_worker(Path(tmp), 1, run_id="C001", follow=False, raw=False, stream=output)
            self.assertIn("New", output.getvalue())
            self.assertIn("new work", output.getvalue())
            self.assertNotIn("Old", output.getvalue())
            self.assertNotIn("old work", output.getvalue())


if __name__ == "__main__":
    unittest.main()
