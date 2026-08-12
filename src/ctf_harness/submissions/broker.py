from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass
from enum import StrEnum

from ctf_harness.platforms import PlatformAdapter


class SubmissionStatus(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    DUPLICATE = "duplicate"
    WRONG_LIMIT = "wrong_limit"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class SubmissionResult:
    status: SubmissionStatus
    message: str = ""

    @property
    def accepted(self) -> bool:
        return self.status is SubmissionStatus.ACCEPTED


class SubmissionBroker:
    """Serialize and de-duplicate platform submissions per challenge."""

    def __init__(
        self,
        platform: PlatformAdapter,
        max_wrong_submissions: int = 3,
        max_network_retries: int = 2,
        retryable_exceptions: tuple[type[BaseException], ...] = (ConnectionError, TimeoutError),
    ) -> None:
        if max_wrong_submissions < 1:
            raise ValueError("max_wrong_submissions must be positive")
        if max_network_retries < 0:
            raise ValueError("max_network_retries cannot be negative")
        self.platform = platform
        self.max_wrong_submissions = max_wrong_submissions
        self.max_network_retries = max_network_retries
        self.retryable_exceptions = retryable_exceptions
        self._locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._submitted: set[tuple[str, str]] = set()
        self._wrong: defaultdict[str, int] = defaultdict(int)

    async def submit(self, challenge_id: str, candidate: str) -> SubmissionResult:
        candidate = candidate.strip()
        if not candidate:
            return SubmissionResult(SubmissionStatus.ERROR, "empty flag candidate")
        async with self._locks[challenge_id]:
            key = (challenge_id, candidate)
            if key in self._submitted:
                return SubmissionResult(SubmissionStatus.DUPLICATE, "candidate already submitted")
            if self._wrong[challenge_id] >= self.max_wrong_submissions:
                return SubmissionResult(SubmissionStatus.WRONG_LIMIT, "wrong submission limit reached")
            self._submitted.add(key)
            for attempt in range(self.max_network_retries + 1):
                try:
                    accepted = await self.platform.submit_flag(challenge_id, candidate)
                    break
                except self.retryable_exceptions as exc:
                    if attempt >= self.max_network_retries:
                        return SubmissionResult(SubmissionStatus.ERROR, f"network submission failed: {exc}")
                    await asyncio.sleep(min(0.25 * (2**attempt), 1.0))
                except Exception as exc:
                    return SubmissionResult(SubmissionStatus.ERROR, f"submission failed: {type(exc).__name__}: {exc}")
            if accepted:
                return SubmissionResult(SubmissionStatus.ACCEPTED)
            self._wrong[challenge_id] += 1
            if self._wrong[challenge_id] >= self.max_wrong_submissions:
                return SubmissionResult(
                    SubmissionStatus.WRONG_LIMIT,
                    "platform rejected candidate; wrong submission limit reached",
                )
            return SubmissionResult(SubmissionStatus.REJECTED, "platform rejected candidate")
