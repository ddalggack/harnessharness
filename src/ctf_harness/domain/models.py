from __future__ import annotations
from dataclasses import dataclass, field
from enum import StrEnum

class RunStatus(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"

class WorkerStatus(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    WAITING_FOR_FEEDBACK = "waiting_for_feedback"
    COMPLETED = "completed"
    FAILED = "failed"
    TERMINATED = "terminated"

@dataclass(frozen=True, slots=True)
class Challenge:
    id: str
    title: str
    category: str
    description: str = ""
    host: str | None = None
    port: int | None = None

@dataclass(frozen=True, slots=True)
class WorkerProfile:
    name: str
    image: str
    tools: tuple[str, ...] = ()

@dataclass(slots=True)
class WorkerRecord:
    worker_id: str
    challenge_id: str
    profile: WorkerProfile
    model: str = "gpt-5.4"
    status: WorkerStatus = WorkerStatus.CREATED
    reports: list[str] = field(default_factory=list)
