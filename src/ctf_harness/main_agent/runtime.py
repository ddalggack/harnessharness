from __future__ import annotations

from pathlib import Path

from ctf_harness.domain import Challenge
from ctf_harness.domain.models import WorkerRecord, WorkerStatus
from ctf_harness.events import EventBus
from ctf_harness.protocol import ReportKind, WorkerAssignment, WorkerReport
from ctf_harness.storage import MemoryRunRepository


class MainAgentRuntime:
    """Deterministic run coordinator that never supervises worker tool actions."""

    def __init__(
        self,
        repository: MemoryRunRepository,
        events: EventBus,
    ) -> None:
        self.repository = repository
        self.events = events

    async def create_assignment(
        self,
        run_id: str,
        worker_id: str,
        challenge: Challenge,
        workspace: Path,
        worker_model: str,
    ) -> WorkerAssignment:
        """Copy platform challenge data into the worker's assignment envelope."""
        return WorkerAssignment(
            run_id=run_id,
            worker_id=worker_id,
            challenge_id=challenge.id,
            challenge_title=challenge.title,
            challenge_category=challenge.category,
            challenge_description=challenge.description,
            workspace_uri=str(workspace),
            model=worker_model,
            host=challenge.host,
            port=challenge.port,
        )

    async def handle_report(self, report: WorkerReport) -> None:
        await self.repository.append_report(report.worker_id, report)
        await self.events.publish(
            "worker.reported",
            worker_id=report.worker_id,
            challenge_id=report.challenge_id,
            kind=report.kind.value,
            summary=report.summary,
            artifacts=report.artifacts,
            flag_candidate=report.flag_candidate,
        )
        if report.kind is ReportKind.COMPLETED:
            await self.repository.set_worker_status(
                report.worker_id, WorkerStatus.COMPLETED
            )
        elif report.kind is ReportKind.FAILED:
            await self.repository.set_worker_status(report.worker_id, WorkerStatus.FAILED)
        return None

    async def register(self, record: WorkerRecord) -> None:
        await self.repository.add_worker(record)
        await self.repository.set_worker_status(record.worker_id, WorkerStatus.RUNNING)
        await self.events.publish(
            "worker.started",
            worker_id=record.worker_id,
            challenge_id=record.challenge_id,
        )

    async def terminate(self, worker_id: str) -> None:
        await self.repository.set_worker_status(worker_id, WorkerStatus.TERMINATED)
        await self.events.publish("worker.terminated", worker_id=worker_id)
