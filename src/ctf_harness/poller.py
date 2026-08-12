from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from enum import StrEnum

from ctf_harness.domain import Challenge
from ctf_harness.platforms import PlatformAdapter

logger = logging.getLogger(__name__)


class PollEventKind(StrEnum):
    NEW_CHALLENGE = "new_challenge"
    CHALLENGE_SOLVED = "challenge_solved"


@dataclass(frozen=True, slots=True)
class PollEvent:
    kind: PollEventKind
    challenge_id: str
    challenge: Challenge | None = None


class PlatformPoller:
    """Turn platform state changes into coarse-grained coordinator events."""

    def __init__(self, platform: PlatformAdapter, interval_s: float = 5.0) -> None:
        if interval_s <= 0:
            raise ValueError("interval_s must be positive")
        self.platform = platform
        self.interval_s = interval_s
        self._known_challenges: dict[str, Challenge] = {}
        self._known_solved: set[str] = set()
        self._events: asyncio.Queue[PollEvent] = asyncio.Queue()
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    @property
    def known_challenge_ids(self) -> set[str]:
        return set(self._known_challenges)

    @property
    def known_challenges(self) -> tuple[Challenge, ...]:
        return tuple(self._known_challenges.values())

    @property
    def known_solved_ids(self) -> set[str]:
        return set(self._known_solved)

    async def seed(self) -> None:
        """Capture an initial baseline without emitting events."""
        challenges = await self.platform.list_challenges()
        solved = await self.platform.list_solved_challenge_ids()
        self._known_challenges = {challenge.id: challenge for challenge in challenges}
        self._known_solved = set(solved)

    async def start(self) -> None:
        if self._task is not None:
            raise RuntimeError("poller is already running")
        await self.seed()
        self._stop.clear()
        self._task = asyncio.create_task(self._loop(), name="platform-poller")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def poll_once(self) -> None:
        try:
            challenges = await self.platform.list_challenges()
            solved = await self.platform.list_solved_challenge_ids()
        except Exception as exc:
            logger.warning("platform poll failed: %s", exc)
            return

        current = {challenge.id: challenge for challenge in challenges}
        solved_ids = set(solved)

        for challenge_id in sorted(current.keys() - self._known_challenges.keys()):
            self._events.put_nowait(
                PollEvent(PollEventKind.NEW_CHALLENGE, challenge_id, current[challenge_id])
            )
        for challenge_id in sorted(solved_ids - self._known_solved):
            self._events.put_nowait(
                PollEvent(PollEventKind.CHALLENGE_SOLVED, challenge_id)
            )

        self._known_challenges = current
        self._known_solved = solved_ids

    async def get_event(self, timeout: float | None = None) -> PollEvent | None:
        try:
            if timeout is None:
                return await self._events.get()
            return await asyncio.wait_for(self._events.get(), timeout)
        except TimeoutError:
            return None

    def drain_events(self) -> list[PollEvent]:
        events: list[PollEvent] = []
        while True:
            try:
                events.append(self._events.get_nowait())
            except asyncio.QueueEmpty:
                return events

    async def _loop(self) -> None:
        while not self._stop.is_set():
            await asyncio.sleep(self.interval_s)
            await self.poll_once()