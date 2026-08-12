from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from enum import StrEnum


class ReportKind(StrEnum):
    CHECKPOINT = "checkpoint"
    FLAG_CANDIDATE = "flag_candidate"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class WorkerAssignment:
    run_id: str
    worker_id: str
    challenge_id: str
    challenge_title: str
    challenge_category: str
    challenge_description: str
    workspace_uri: str
    model: str = "gpt-5.4"
    worker_number: int = 0
    host: str | None = None
    port: int | None = None


@dataclass(frozen=True, slots=True)
class WorkerReport:
    run_id: str
    worker_id: str
    challenge_id: str
    kind: ReportKind
    summary: str
    artifacts: tuple[str, ...] = ()
    flag_candidate: str | None = None


def to_json(message: WorkerAssignment | WorkerReport) -> str:
    payload = asdict(message)
    payload["type"] = type(message).__name__
    return json.dumps(payload, ensure_ascii=False)
