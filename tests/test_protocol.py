import json
import unittest
from ctf_harness.protocol import ReportKind, WorkerReport
from ctf_harness.protocol.messages import to_json

class ProtocolTests(unittest.TestCase):
    def test_json(self):
        payload = json.loads(to_json(WorkerReport("r", "w", "c", ReportKind.CHECKPOINT, "ready")))
        self.assertEqual(payload["type"], "WorkerReport")
        self.assertEqual(payload["kind"], "checkpoint")
