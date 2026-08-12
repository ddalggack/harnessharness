from __future__ import annotations
import json
from dataclasses import asdict, dataclass
from enum import StrEnum

class ReportKind(StrEnum):
    CHECKPOINT = "checkpoint"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    FAILED = "failed"

@dataclass(frozen=True, slots=True)
class WorkerAssignment:
    run_id: str
    worker_id: str
    challenge_id: str
    objective: str
    workspace_uri: str
    profile: str
    model: str = "gpt-5.4"

@dataclass(frozen=True, slots=True)
class WorkerReport:
    run_id: str
    worker_id: str
    challenge_id: str
    kind: ReportKind
    summary: str
    artifacts: tuple[str, ...] = ()
    flag_candidate: str | None = None

@dataclass(frozen=True, slots=True)
class Feedback:
    run_id: str
    worker_id: str
    directive: str

def to_json(message: WorkerAssignment | WorkerReport | Feedback) -> str:
    payload = asdict(message)
    payload["type"] = type(message).__name__
    return json.dumps(payload, ensure_ascii=False)
