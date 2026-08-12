import asyncio
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from ctf_harness.platforms import CTFdPlatformAdapter


class _CTFdHandler(BaseHTTPRequestHandler):
    requests: list[tuple[str, str, str | None, dict | None]] = []
    external_file_url: str | None = None

    def log_message(self, format, *args):
        return None

    def _json(self, payload, status=200):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        type(self).requests.append(("GET", self.path, self.headers.get("Authorization"), None))
        if self.path == "/api/v1/challenges":
            self._json(
                {
                    "success": True,
                    "data": [
                        {
                            "id": 1,
                            "name": "baby-bof",
                            "category": "pwn",
                            "solved_by_me": False,
                        },
                        {
                            "id": 2,
                            "name": "tiny-rev",
                            "category": "rev",
                            "solved_by_me": True,
                        },
                    ],
                }
            )
        elif self.path == "/api/v1/challenges/1":
            self._json(
                {
                    "success": True,
                    "data": {
                        "id": 1,
                        "name": "baby-bof",
                        "category": "pwn",
                        "description": "Find the overflow",
                        "connection_info": "nc challenge.local 31337",
                        "files": [
                            type(self).external_file_url
                            or "/files/abc/exploit.bin?token=signed"
                        ],
                    },
                }
            )
        elif self.path == "/api/v1/challenges/2":
            self._json(
                {
                    "success": True,
                    "data": {
                        "id": 2,
                        "name": "tiny-rev",
                        "category": "rev",
                        "description": "Reverse it",
                        "connection_info": "http://web.local:8080/path",
                        "files": [],
                    },
                }
            )
        elif self.path == "/files/abc/exploit.bin?token=signed":
            body = b"challenge-bytes"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self._json({"success": False, "errors": ["not found"]}, 404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length) or b"{}")
        type(self).requests.append(
            ("POST", self.path, self.headers.get("Authorization"), payload)
        )
        if self.path == "/api/v1/challenges/attempt":
            status = "correct" if payload.get("submission") == "FLAG{ok}" else "incorrect"
            self._json({"success": True, "data": {"status": status}})
        else:
            self._json({"success": False}, 404)


class CTFdPlatformAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _CTFdHandler.requests = []
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _CTFdHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        _CTFdHandler.requests.clear()
        _CTFdHandler.external_file_url = None

    def test_lists_detailed_challenges_and_solved_ids_with_token_auth(self):
        async def scenario():
            adapter = CTFdPlatformAdapter(self.base_url, token="secret-token")
            challenges = await adapter.list_challenges()
            solved = await adapter.list_solved_challenge_ids()

            self.assertEqual([challenge.id for challenge in challenges], ["1", "2"])
            self.assertEqual(challenges[0].description, "Find the overflow")
            self.assertEqual((challenges[0].host, challenges[0].port), ("challenge.local", 31337))
            self.assertEqual((challenges[1].host, challenges[1].port), ("web.local", 8080))
            self.assertIn("http://web.local:8080/path", challenges[1].description)
            self.assertEqual(solved, {"2"})
            self.assertTrue(
                all(request[2] == "Token secret-token" for request in _CTFdHandler.requests)
            )

        asyncio.run(scenario())

    def test_downloads_challenge_files_and_submits_flags(self):
        async def scenario():
            adapter = CTFdPlatformAdapter(self.base_url, token="secret-token")
            challenge = (await adapter.list_challenges())[0]
            with tempfile.TemporaryDirectory() as tmp:
                destination = await adapter.download_challenge(challenge, Path(tmp))
                self.assertEqual(
                    (destination / "exploit.bin").read_bytes(), b"challenge-bytes"
                )
                metadata = json.loads((destination / "challenge.json").read_text())
                self.assertEqual(metadata["description"], "Find the overflow")

            self.assertTrue(await adapter.submit_flag("1", "FLAG{ok}"))
            self.assertFalse(await adapter.submit_flag("1", "FLAG{no}"))
            attempts = [request for request in _CTFdHandler.requests if request[0] == "POST"]
            self.assertEqual(
                attempts[0][3], {"challenge_id": 1, "submission": "FLAG{ok}"}
            )

        asyncio.run(scenario())

    def test_does_not_forward_ctfd_token_to_external_file_host(self):
        class ExternalHandler(BaseHTTPRequestHandler):
            authorization: str | None = None

            def log_message(self, format, *args):
                return None

            def do_GET(self):
                type(self).authorization = self.headers.get("Authorization")
                body = b"external-file"
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        external = ThreadingHTTPServer(("127.0.0.1", 0), ExternalHandler)
        thread = threading.Thread(target=external.serve_forever, daemon=True)
        thread.start()
        _CTFdHandler.external_file_url = (
            f"http://127.0.0.1:{external.server_port}/artifact.bin?signature=signed"
        )
        try:
            async def scenario():
                adapter = CTFdPlatformAdapter(self.base_url, token="secret-token")
                challenge = (await adapter.list_challenges())[0]
                with tempfile.TemporaryDirectory() as tmp:
                    destination = await adapter.download_challenge(challenge, Path(tmp))
                    self.assertEqual(
                        (destination / "artifact.bin").read_bytes(), b"external-file"
                    )

            asyncio.run(scenario())
            self.assertIsNone(ExternalHandler.authorization)
        finally:
            external.shutdown()
            external.server_close()
            thread.join()


if __name__ == "__main__":
    unittest.main()
