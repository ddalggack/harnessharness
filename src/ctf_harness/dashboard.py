from __future__ import annotations

import asyncio
import json
import threading
from concurrent.futures import Future
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from ctf_harness.app import CodexHarness, build_codex_harness
from ctf_harness.domain import Challenge
from ctf_harness.events import EventBus
from ctf_harness.main_agent import MainAgentRuntime
from ctf_harness.platforms import CTFdPlatformAdapter, MemoryPlatformAdapter, PlatformAdapter
from ctf_harness.scheduler import LocalWorkerScheduler
from ctf_harness.storage import LocalObjectStore, MemoryRunRepository
from ctf_harness.worker import DemoWorkerRunner
from ctf_harness.workflows import CtfRunWorkflow

_CONSOLE_ROOT = Path(__file__).with_name("console")
_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".ttf": "font/ttf",
    ".txt": "text/plain; charset=utf-8",
    ".md": "text/markdown; charset=utf-8",
}
_DEMO_CHALLENGES = (
    Challenge("pwn-01", "Warm-up Stack", "pwn", "Demo lifecycle challenge"),
    Challenge("pwn-02", "Tiny ROP", "pwn", "Demo lifecycle challenge"),
    Challenge("pwn-03", "Echo Chamber", "pwn", "Demo lifecycle challenge"),
    Challenge("rev-01", "Lost Password", "rev", "Demo lifecycle challenge"),
    Challenge("rev-02", "Switch Maze", "rev", "Demo lifecycle challenge"),
    Challenge("rev-03", "Packed Note", "rev", "Demo lifecycle challenge"),
    Challenge("web-01", "Cookie Jar", "web", "Demo lifecycle challenge"),
    Challenge("web-02", "Header Puzzle", "web", "Demo lifecycle challenge"),
    Challenge("web-03", "Local Gallery", "web", "Demo lifecycle challenge"),
)


class CategoryFilteredPlatform:
    def __init__(self, platform: PlatformAdapter, categories: set[str]) -> None:
        self.platform = platform
        self.categories = categories

    async def list_challenges(self) -> list[Challenge]:
        return [
            challenge
            for challenge in await self.platform.list_challenges()
            if challenge.category.lower() in self.categories
        ]

    async def list_solved_challenge_ids(self) -> set[str]:
        visible_ids = {challenge.id for challenge in await self.list_challenges()}
        return (await self.platform.list_solved_challenge_ids()) & visible_ids

    async def download_challenge(self, challenge: Challenge, destination: Path) -> Path:
        return await self.platform.download_challenge(challenge, destination)

    async def submit_flag(self, challenge_id: str, flag: str) -> bool:
        return await self.platform.submit_flag(challenge_id, flag)


class DashboardController:
    def __init__(self, runs_root: Path) -> None:
        self.runs_root = runs_root.resolve()
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, name="ddalggack-dashboard", daemon=True)
        self.thread.start()
        self.platform: PlatformAdapter | None = None
        self.harness: CodexHarness | None = None
        self.challenges: list[Challenge] = []
        self.mode = "demo"
        self.connection_label = ""
        self.worker_limit = 3
        self.auto_submit_flags = False
        self.run_id: str | None = None
        self.run_future: Future[Any] | None = None
        self.last_error: str | None = None

    def _submit(self, coroutine: Any) -> Any:
        return asyncio.run_coroutine_threadsafe(coroutine, self.loop).result(timeout=35)

    async def _stop_async(self) -> None:
        if self.run_future is not None and not self.run_future.done():
            self.run_future.cancel()

    def stop(self) -> dict[str, Any]:
        if self.run_future is not None and not self.run_future.done():
            self.run_future.cancel()
            try:
                self.run_future.result(timeout=10)
            except BaseException:
                pass
        return self.public_state()

    def reset(self) -> dict[str, Any]:
        self.stop()
        self.platform = None
        self.harness = None
        self.challenges = []
        self.connection_label = ""
        self.auto_submit_flags = False
        self.run_id = None
        self.run_future = None
        self.last_error = None
        return self.public_state()

    def connect(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self.run_future is not None and not self.run_future.done():
            raise RuntimeError("실행 중에는 연결 설정을 바꿀 수 없습니다.")
        mode = str(payload.get("mode", "demo"))
        categories = {str(item).lower() for item in payload.get("categories", [])}
        if not categories:
            raise ValueError("문제 분야를 하나 이상 선택해야 합니다.")
        worker_limit = int(payload.get("workerLimit", 3))
        if not 1 <= worker_limit <= 3:
            raise ValueError("동시 Worker 수는 1~3이어야 합니다.")
        auto_submit_flags = bool(payload.get("autoSubmitFlags", False))

        if mode == "demo":
            auto_submit_flags = False
            challenges = [item for item in _DEMO_CHALLENGES if item.category in categories]
            platform: PlatformAdapter = MemoryPlatformAdapter(challenges)
            label = "안전 Demo"
        elif mode == "ctfd":
            if payload.get("authorized") is not True:
                raise ValueError("허가된 CTFd 대상임을 확인해야 합니다.")
            base_url = str(payload.get("baseUrl", "")).strip()
            if not base_url:
                raise ValueError("CTFd URL을 입력해야 합니다.")
            ctfd = CTFdPlatformAdapter(base_url, token=str(payload.get("apiToken") or "") or None)
            platform = CategoryFilteredPlatform(ctfd, categories)
            challenges = self._submit(platform.list_challenges())
            label = urlparse(base_url).netloc or base_url
        else:
            raise ValueError("지원하지 않는 실행 모드입니다.")

        self.stop()
        self.mode = mode
        self.platform = platform
        self.challenges = challenges
        self.connection_label = label
        self.worker_limit = worker_limit
        self.auto_submit_flags = auto_submit_flags
        self.harness = None
        self.run_id = None
        self.run_future = None
        self.last_error = None
        return self.public_state()

    def _build_harness(self) -> CodexHarness:
        assert self.platform is not None
        if self.mode == "ctfd":
            return build_codex_harness(
                platform=self.platform,
                runs_root=self.runs_root,
                max_swarms=self.worker_limit,
                submit_flags=self.auto_submit_flags,
            )
        repository, events = MemoryRunRepository(), EventBus()
        scheduler = LocalWorkerScheduler(DemoWorkerRunner, max_workers=self.worker_limit)
        workflow = CtfRunWorkflow(
            self.platform,
            scheduler,
            repository,
            LocalObjectStore(self.runs_root),
            events,
            main=MainAgentRuntime(repository, events),
        )
        return CodexHarness(workflow, scheduler, repository, events)

    def start(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        if self.platform is None:
            raise RuntimeError("먼저 환경을 확인해야 합니다.")
        if self.run_future is not None and not self.run_future.done():
            raise RuntimeError("이미 실행 중입니다.")
        requested_auto_submit = bool((payload or {}).get("autoSubmitFlags", False))
        self.auto_submit_flags = self.mode == "ctfd" and requested_auto_submit
        self.harness = self._build_harness()
        self.run_id = datetime.now(timezone.utc).strftime("dashboard-%Y%m%d-%H%M%S")
        self.last_error = None
        self.run_future = asyncio.run_coroutine_threadsafe(
            self.harness.workflow.run(self.run_id), self.loop
        )
        return self.public_state()

    def _worker_rows(self) -> list[dict[str, Any]]:
        records = list(self.harness.repository.workers.values()) if self.harness else []
        rows = []
        for index in range(3):
            if index < len(records):
                record = records[index]
                challenge = next((c for c in self.challenges if c.id == record.challenge_id), None)
                rows.append({
                    "name": f"Worker {index + 1}",
                    "enabled": index < self.worker_limit,
                    "status": record.status.value,
                    "challengeName": challenge.title if challenge else record.challenge_id,
                    "profile": record.profile.name,
                    "phase": record.reports[-1].kind.value if record.reports else "assigned",
                    "progress": 100 if record.status.value in {"completed", "failed", "terminated"} else 50,
                    "completed": int(record.status.value == "completed"),
                })
            else:
                rows.append({
                    "name": f"Worker {index + 1}",
                    "enabled": index < self.worker_limit,
                    "status": "idle",
                    "challengeName": "",
                    "profile": "",
                    "phase": "대기 중" if index < self.worker_limit else "Concurrency 제한",
                    "progress": 0,
                    "completed": 0,
                })
        return rows

    def _challenge_rows(self) -> list[dict[str, Any]]:
        by_challenge = {
            record.challenge_id: record
            for record in (self.harness.repository.workers.values() if self.harness else [])
        }
        rows = []
        for challenge in self.challenges:
            record = by_challenge.get(challenge.id)
            status = "queued"
            phase = "실행 대기"
            worker_id = None
            if record is not None:
                worker_id = record.worker_id
                status = record.status.value
                phase = record.reports[-1].summary if record.reports else "Worker 시작"
            rows.append({
                "id": challenge.id,
                "name": challenge.title,
                "category": challenge.category.lower(),
                "points": 0,
                "workerId": worker_id,
                "status": status,
                "phase": phase,
            })
        return rows

    def _event_rows(self) -> list[dict[str, str]]:
        if not self.harness:
            return []
        rows = []
        for event in reversed(self.harness.events.history[-80:]):
            level = "error" if "failed" in event.type else "success" if event.type.endswith("completed") else "info"
            subject = event.payload.get("challenge_id") or event.payload.get("worker_id") or event.payload.get("run_id") or ""
            kind = event.payload.get("kind")
            message = " · ".join(str(item) for item in (subject, kind) if item)
            rows.append({"time": event.occurred_at, "type": event.type, "message": message, "level": level})
        return rows

    def public_state(self) -> dict[str, Any]:
        running = self.run_future is not None and not self.run_future.done()
        status = "running" if running else "idle"
        message = "왼쪽에서 Demo 또는 CTFd 환경을 먼저 확인합니다."
        if self.platform is not None:
            status = "ready"
            message = f"{len(self.challenges)}개 문제를 실행할 준비가 되었습니다."
        if running:
            status = "running"
            message = "Worker가 문제를 처리하고 있습니다."
        elif self.run_future is not None and self.run_future.done():
            try:
                self.run_future.result()
            except BaseException as exc:
                if type(exc).__name__ == "CancelledError":
                    status, message = "stopped", "사용자가 실행을 중지했습니다."
                else:
                    self.last_error = f"{type(exc).__name__}: {exc}"
                    status, message = "failed", self.last_error
            else:
                status, message = "completed", "모든 Worker 실행이 끝났습니다."

        challenge_rows = self._challenge_rows()
        terminal = sum(item["status"] in {"completed", "failed", "terminated"} for item in challenge_rows)
        completed = sum(item["status"] == "completed" for item in challenge_rows)
        total = len(challenge_rows)
        progress = round(terminal * 100 / total) if total else 0
        return {
            "environment": {"pythonVersion": __import__("platform").python_version()},
            "connection": {"connected": self.platform is not None, "label": self.connection_label},
            "config": {
                "workerLimit": self.worker_limit,
                "mode": self.mode,
                "autoSubmitFlags": self.auto_submit_flags,
            },
            "run": {
                "id": self.run_id,
                "status": status,
                "message": message,
                "total": total,
                "solved": completed,
                "progress": progress,
            },
            "workers": self._worker_rows(),
            "challenges": challenge_rows,
            "events": self._event_rows(),
        }

    def shutdown(self) -> None:
        self.stop()
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=2)
        self.loop.close()


class DashboardRequestHandler(BaseHTTPRequestHandler):
    controller: DashboardController

    def _json(self, status: int, value: Any) -> None:
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict[str, Any]:
        size = int(self.headers.get("Content-Length", "0"))
        if size > 1024 * 1024:
            raise ValueError("요청 본문이 너무 큽니다.")
        if size == 0:
            return {}
        value = json.loads(self.rfile.read(size))
        if not isinstance(value, dict):
            raise ValueError("JSON 객체가 필요합니다.")
        return value

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/health":
            self._json(HTTPStatus.OK, {"ok": True})
            return
        if path == "/api/state":
            self._json(HTTPStatus.OK, self.controller.public_state())
            return
        relative = "index.html" if path == "/" else unquote(path.lstrip("/"))
        target = (_CONSOLE_ROOT / relative).resolve()
        if _CONSOLE_ROOT.resolve() not in target.parents:
            self._json(HTTPStatus.FORBIDDEN, {"error": "허용되지 않은 경로입니다."})
            return
        try:
            body = target.read_bytes()
        except OSError:
            self._json(HTTPStatus.NOT_FOUND, {"error": "파일을 찾을 수 없습니다."})
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", _CONTENT_TYPES.get(target.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        try:
            path = urlparse(self.path).path
            if path == "/api/connect":
                value = self.controller.connect(self._body())
                status = HTTPStatus.OK
            elif path == "/api/run":
                value = self.controller.start(self._body())
                status = HTTPStatus.ACCEPTED
            elif path == "/api/stop":
                value = self.controller.stop()
                status = HTTPStatus.OK
            elif path == "/api/reset":
                value = self.controller.reset()
                status = HTTPStatus.OK
            else:
                self._json(HTTPStatus.NOT_FOUND, {"error": "API 경로를 찾을 수 없습니다."})
                return
            self._json(status, value)
        except (ValueError, RuntimeError, json.JSONDecodeError) as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except Exception as exc:
            self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": f"{type(exc).__name__}: {exc}"})

    def log_message(self, format: str, *args: Any) -> None:
        return


def serve_dashboard(host: str = "127.0.0.1", port: int = 8788, runs_root: Path = Path("runs")) -> None:
    controller = DashboardController(runs_root)
    handler = type("BoundDashboardHandler", (DashboardRequestHandler,), {"controller": controller})
    server = ThreadingHTTPServer((host, port), handler)
    print(f"Ddalggack dashboard: http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        controller.shutdown()
