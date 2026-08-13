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
from urllib.parse import parse_qs, unquote, urlparse

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

    @staticmethod
    def _compact(value: Any, limit: int = 120) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            text = " ".join(value.split())
        else:
            try:
                text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
            except (TypeError, ValueError):
                text = str(value)
        return text if len(text) <= limit else f"{text[: limit - 1]}…"

    @staticmethod
    def _number(value: Any) -> int | None:
        return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None

    @classmethod
    def _normalize_token_usage(cls, usage: Any) -> dict[str, int] | None:
        if isinstance(usage, (int, float)) and not isinstance(usage, bool):
            return {"totalTokens": max(0, int(usage))}
        if not isinstance(usage, dict):
            return None

        total = usage.get("total")
        source = total if isinstance(total, dict) else usage

        def pick(*names: str) -> int | None:
            for container in (source, usage):
                for name in names:
                    number = cls._number(container.get(name))
                    if number is not None:
                        return max(0, number)
            return None

        normalized = {
            "totalTokens": pick("totalTokens", "total_tokens"),
            "inputTokens": pick("inputTokens", "input_tokens"),
            "cachedInputTokens": pick("cachedInputTokens", "cached_input_tokens"),
            "outputTokens": pick("outputTokens", "output_tokens"),
            "reasoningOutputTokens": pick(
                "reasoningOutputTokens", "reasoning_output_tokens"
            ),
            "modelContextWindow": pick("modelContextWindow", "model_context_window"),
        }
        return {key: value for key, value in normalized.items() if value is not None} or None

    @classmethod
    def _event_phase(cls, event_type: str, payload: dict[str, Any]) -> str | None:
        if event_type == "worker.started":
            return "Worker 시작"
        if event_type == "worker.turn_started":
            return f"Turn {payload.get('turn', 1)} 분석 중"
        if event_type == "worker.turn_completed":
            return f"Turn {payload.get('turn', 1)} 완료"
        if event_type == "worker.heartbeat":
            return cls._compact(payload.get("summary")) or "작업 중"
        if event_type == "worker.reasoning":
            return cls._compact(payload.get("summary")) or "추론 중"
        if event_type == "worker.tool":
            tool = cls._compact(payload.get("tool")) or "도구"
            return f"{tool} {'완료' if payload.get('status') == 'completed' else '실행 중'}"
        if event_type == "worker.file_change":
            return "파일 변경"
        if event_type == "worker.message":
            return "응답 작성 중"
        if event_type == "worker.reported":
            return str(payload.get("kind") or "보고 완료")
        if event_type == "worker.terminated":
            return "중지"
        if event_type.startswith("submission."):
            return cls._compact(payload.get("summary")) or event_type.split(".", 1)[1]
        return None

    def _worker_event_index(
        self, events: list[Any]
    ) -> tuple[dict[str, int], dict[str, dict[str, Any]]]:
        slot_by_worker: dict[str, int] = {}
        metadata: dict[str, dict[str, Any]] = {}
        for order, event in enumerate(events):
            payload = event.payload
            worker_id = str(payload.get("worker_id") or "")
            if not worker_id:
                continue
            slot = self._number(payload.get("worker_number"))
            if slot is not None and 1 <= slot <= 3:
                slot_by_worker[worker_id] = slot
            item = metadata.setdefault(worker_id, {"order": order})
            item["order"] = order
            if event.type == "worker.started":
                item.setdefault("startedAt", event.occurred_at)
            if event.type == "worker.token_usage":
                normalized = self._normalize_token_usage(payload.get("usage"))
                if normalized is not None:
                    item["tokenUsage"] = normalized
            phase = self._event_phase(event.type, payload)
            if phase:
                item["phase"] = phase
            if event.type == "worker.reported" and payload.get("kind") in {"completed", "failed"}:
                item["finishedAt"] = event.occurred_at
            elif event.type == "worker.terminated":
                item["finishedAt"] = event.occurred_at
        return slot_by_worker, metadata

    def _worker_rows(
        self, records: list[Any], events: list[Any]
    ) -> tuple[list[dict[str, Any]], dict[str, int]]:
        slot_by_worker, metadata = self._worker_event_index(events)
        used_slots = set(slot_by_worker.values())
        for record in records:
            if record.worker_id in slot_by_worker:
                continue
            slot = next((candidate for candidate in range(1, 4) if candidate not in used_slots), None)
            if slot is None:
                slot = 1 + min(
                    range(3),
                    key=lambda index: sum(value == index + 1 for value in slot_by_worker.values()),
                )
            slot_by_worker[record.worker_id] = slot
            used_slots.add(slot)

        rows: list[dict[str, Any]] = []
        for slot in range(1, 4):
            assigned = [record for record in records if slot_by_worker.get(record.worker_id) == slot]
            completed = sum(record.status.value == "completed" for record in assigned)
            active = [record for record in assigned if record.status.value in {"created", "running"}]
            candidates = active or assigned
            current = max(
                candidates,
                key=lambda record: metadata.get(record.worker_id, {}).get("order", -1),
                default=None,
            )
            if current is None:
                rows.append({
                    "workerId": None,
                    "name": f"Worker {slot}",
                    "enabled": slot <= self.worker_limit,
                    "status": "idle",
                    "challengeName": "",
                    "profile": "",
                    "model": "",
                    "phase": "대기 중" if slot <= self.worker_limit else "Concurrency 제한",
                    "progress": 0,
                    "completed": completed,
                    "tokenUsage": None,
                    "startedAt": None,
                    "finishedAt": None,
                })
                continue

            challenge = next((item for item in self.challenges if item.id == current.challenge_id), None)
            current_metadata = metadata.get(current.worker_id, {})
            status = current.status.value
            report_phase = current.reports[-1].kind.value if current.reports else "assigned"
            rows.append({
                "workerId": current.worker_id,
                "name": f"Worker {slot}",
                "enabled": slot <= self.worker_limit,
                "status": status,
                "challengeName": challenge.title if challenge else current.challenge_id,
                "profile": current.profile.name,
                "model": current.model,
                "phase": current_metadata.get("phase", report_phase),
                "progress": 100 if status in {"completed", "failed", "terminated"} else 50,
                "completed": completed,
                "tokenUsage": current_metadata.get("tokenUsage"),
                "startedAt": current_metadata.get("startedAt"),
                "finishedAt": current_metadata.get("finishedAt"),
            })
        return rows, slot_by_worker

    def _challenge_rows(
        self, records: list[Any], slot_by_worker: dict[str, int]
    ) -> list[dict[str, Any]]:
        by_challenge = {record.challenge_id: record for record in records}
        rows = []
        for challenge in self.challenges:
            record = by_challenge.get(challenge.id)
            status = "queued"
            phase = "실행 대기"
            worker_id = None
            backend_worker_id = None
            if record is not None:
                backend_worker_id = record.worker_id
                slot = slot_by_worker.get(record.worker_id)
                worker_id = f"Worker {slot}" if slot is not None else record.worker_id
                status = record.status.value
                phase = record.reports[-1].summary if record.reports else "Worker 시작"
            rows.append({
                "id": challenge.id,
                "name": challenge.title,
                "category": challenge.category.lower(),
                "points": 0,
                "workerId": worker_id,
                "backendWorkerId": backend_worker_id,
                "status": status,
                "phase": phase,
            })
        return rows

    def event_batch(self, cursor: int, limit: int = 200) -> dict[str, Any]:
        if cursor < 0:
            raise ValueError("event cursor는 0 이상이어야 합니다.")
        history = list(self.harness.events.history) if self.harness else []
        if cursor > len(history):
            cursor = 0
        selected = history[cursor : cursor + max(1, min(limit, 500))]
        return {
            "runId": self.run_id,
            "cursor": cursor,
            "nextCursor": cursor + len(selected),
            "hasMore": cursor + len(selected) < len(history),
            "events": [
                {
                    "sequence": cursor + index + 1,
                    "type": event.type,
                    "occurredAt": event.occurred_at,
                    "payload": event.payload,
                }
                for index, event in enumerate(selected)
            ],
        }

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

        records = list(self.harness.repository.workers.values()) if self.harness else []
        events = list(self.harness.events.history) if self.harness else []
        worker_rows, slot_by_worker = self._worker_rows(records, events)
        challenge_rows = self._challenge_rows(records, slot_by_worker)
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
            "workers": worker_rows,
            "challenges": challenge_rows,
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
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/api/health":
            self._json(HTTPStatus.OK, {"ok": True})
            return
        if path == "/api/state":
            self._json(HTTPStatus.OK, self.controller.public_state())
            return
        if path == "/api/events":
            try:
                cursor = int(parse_qs(parsed.query).get("cursor", ["0"])[0])
                value = self.controller.event_batch(cursor)
            except (TypeError, ValueError) as exc:
                self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            else:
                self._json(HTTPStatus.OK, value)
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
