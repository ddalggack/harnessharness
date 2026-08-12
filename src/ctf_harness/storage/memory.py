from pathlib import Path
from ctf_harness.domain.models import WorkerRecord, WorkerStatus
from ctf_harness.protocol import WorkerReport

class MemoryRunRepository:
    def __init__(self) -> None:
        self.workers: dict[str, WorkerRecord] = {}
    async def add_worker(self, record: WorkerRecord) -> None:
        self.workers[record.worker_id] = record
    async def set_worker_status(self, worker_id: str, status: WorkerStatus) -> None:
        self.workers[worker_id].status = status
    async def append_report(self, worker_id: str, report: WorkerReport) -> None:
        self.workers[worker_id].reports.append(report)

class LocalObjectStore:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
    def challenge_workspace(self, run_id: str, challenge_id: str) -> Path:
        path = (self.root / run_id / "challenges" / challenge_id).resolve()
        if self.root not in path.parents:
            raise ValueError("workspace escaped object-store root")
        path.mkdir(parents=True, exist_ok=True)
        return path
