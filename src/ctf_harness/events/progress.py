from __future__ import annotations

import asyncio
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path
from typing import TextIO

from .bus import Event, EventBus


class ProgressReporter:
    """Persist run events and optionally mirror concise progress to stderr."""

    def __init__(
        self,
        events: EventBus,
        runs_root: Path,
        run_id: str,
        console_mode: str = "human",
        stream: TextIO | None = None,
    ) -> None:
        if console_mode not in {"human", "json", "quiet"}:
            raise ValueError("console_mode must be human, json, or quiet")
        self.events = events
        self.run_id = run_id
        self.run_dir = runs_root.resolve() / run_id
        self.console_mode = console_mode
        self.stream = stream or sys.stderr
        self._queue: asyncio.Queue[Event] | None = None
        self._task: asyncio.Task[None] | None = None
        self._stop = object()
        self._workers: dict[str, dict[str, object]] = {}
        self.error: str | None = None

    async def start(self) -> None:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self._queue = self.events.subscribe()
        self._task = asyncio.create_task(self._consume())

    async def stop(self) -> None:
        if self._queue is None or self._task is None:
            return
        await self._queue.put(self._stop)  # type: ignore[arg-type]
        await self._task
        self.events.unsubscribe(self._queue)
        self._queue = None
        self._task = None

    async def _consume(self) -> None:
        assert self._queue is not None
        while True:
            item = await self._queue.get()
            if item is self._stop:
                return
            if self.error is None:
                try:
                    await asyncio.to_thread(self._record, item)
                except Exception as exc:
                    self.error = f"{type(exc).__name__}: {exc}"
                    try:
                        print(
                            f"progress persistence disabled: {self.error}",
                            file=sys.stderr,
                            flush=True,
                        )
                    except Exception:
                        pass
            try:
                self._print(item)
            except Exception:
                pass

    def _record(self, event: Event) -> None:
        payload = asdict(event)
        with (self.run_dir / "events.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
        worker_id = event.payload.get("worker_id")
        if isinstance(worker_id, str):
            worker = self._workers.setdefault(worker_id, {})
            worker.update(
                challenge_id=event.payload.get("challenge_id", worker.get("challenge_id")),
                challenge_title=event.payload.get("challenge_title", worker.get("challenge_title")),
                challenge_category=event.payload.get("challenge_category", worker.get("challenge_category")),
                worker_number=event.payload.get("worker_number", worker.get("worker_number")),
                state=self._state(event),
                last_event=event.type,
                summary=event.payload.get("summary", worker.get("summary")),
                updated_at=event.occurred_at,
            )
            if event.type.startswith("submission."):
                worker["submission_status"] = event.type.removeprefix("submission.")
        status = {
            "run_id": self.run_id,
            "updated_at": event.occurred_at,
            "last_event": event.type,
            "workers": self._workers,
        }
        temporary = self.run_dir / ".status.json.tmp"
        temporary.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, self.run_dir / "status.json")

    @staticmethod
    def _state(event: Event) -> str:
        if event.type.startswith("submission."):
            return event.type.removeprefix("submission.")
        if event.type == "worker.reported":
            return str(event.payload.get("kind", "reported"))
        return event.type.removeprefix("worker.")

    def _print(self, event: Event) -> None:
        if self.console_mode == "quiet":
            return
        payload = asdict(event)
        if self.console_mode == "json":
            print(json.dumps(payload, ensure_ascii=False), file=self.stream, flush=True)
            return
        if event.type not in {"worker.started", "submission.accepted"}:
            return
        number = event.payload.get("worker_number", "?")
        title = event.payload.get("challenge_title") or event.payload.get("challenge_id", "unknown")
        verb = "handles" if event.type == "worker.started" else "solved"
        color = self._worker_color(number)
        reset = "\033[0m" if color else ""
        print(f"{color}[{number}] worker {verb} {title}{reset}", file=self.stream, flush=True)

    def _worker_color(self, worker_number: object) -> str:
        is_tty = bool(getattr(self.stream, "isatty", lambda: False)())
        if not is_tty or os.environ.get("NO_COLOR") is not None:
            return ""
        palette = ("\033[36m", "\033[35m", "\033[33m", "\033[34m", "\033[32m", "\033[31m")
        try:
            index = (int(worker_number) - 1) % len(palette)
        except (TypeError, ValueError):
            index = 0
        return "\033[1m" + palette[index]
