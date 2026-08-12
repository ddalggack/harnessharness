from __future__ import annotations

from pathlib import Path
from typing import Protocol

from ctf_harness.domain import Challenge
from ctf_harness.domain.models import WorkerRecord, WorkerStatus
from ctf_harness.events import EventBus
from ctf_harness.protocol import Feedback, ReportKind, WorkerAssignment, WorkerReport
from ctf_harness.storage import MemoryRunRepository


class CoordinatorBackend(Protocol):
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def create_assignment(
        self,
        run_id: str,
        worker_id: str,
        challenge: Challenge,
        workspace: Path,
        worker_model: str,
    ) -> WorkerAssignment: ...
    async def review_report(self, report: WorkerReport) -> Feedback | None: ...


class DeterministicCoordinatorBackend:
    """Offline lifecycle backend used only by tests and the smoke command."""

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def create_assignment(
        self,
        run_id: str,
        worker_id: str,
        challenge: Challenge,
        workspace: Path,
        worker_model: str,
    ) -> WorkerAssignment:
        return WorkerAssignment(
            run_id,
            worker_id,
            challenge.id,
            f"Solve {challenge.title}; report meaningful checkpoints only.",
            str(workspace),
            challenge.category or "general",
            worker_model,
        )

    async def review_report(self, report: WorkerReport) -> Feedback | None:
        if report.kind is ReportKind.BLOCKED:
            return Feedback(
                report.run_id,
                report.worker_id,
                "Reassess the blocker and report one concrete alternative.",
            )
        return None


class MainAgentRuntime:
    """Lifecycle controller; never supervises individual worker tool actions."""
    def __init__(
        self,
        repository: MemoryRunRepository,
        events: EventBus,
        coordinator: CoordinatorBackend | None = None,
    ):
        self.repository = repository
        self.events = events
        self.coordinator = coordinator or DeterministicCoordinatorBackend()

    async def start(self) -> None:
        await self.coordinator.start()

    async def stop(self) -> None:
        await self.coordinator.stop()

    async def create_assignment(
        self,
        run_id: str,
        worker_id: str,
        challenge: Challenge,
        workspace: Path,
        worker_model: str,
    ) -> WorkerAssignment:
        return await self.coordinator.create_assignment(
            run_id, worker_id, challenge, workspace, worker_model
        )

    async def handle_report(self, report: WorkerReport) -> Feedback | None:
        await self.repository.append_report(report.worker_id, report.summary)
        await self.events.publish("worker.reported", worker_id=report.worker_id, challenge_id=report.challenge_id, kind=report.kind.value, summary=report.summary)
        if report.kind is ReportKind.BLOCKED:
            await self.repository.set_worker_status(report.worker_id, WorkerStatus.WAITING_FOR_FEEDBACK)
        if report.kind is ReportKind.COMPLETED:
            await self.repository.set_worker_status(report.worker_id, WorkerStatus.COMPLETED)
        elif report.kind is ReportKind.FAILED:
            await self.repository.set_worker_status(report.worker_id, WorkerStatus.FAILED)
        feedback = await self.coordinator.review_report(report)
        if feedback is not None:
            await self.events.publish(
                "coordinator.feedback",
                worker_id=report.worker_id,
                challenge_id=report.challenge_id,
                directive=feedback.directive,
            )
        return feedback

    async def register(self, record: WorkerRecord) -> None:
        await self.repository.add_worker(record)
        await self.repository.set_worker_status(record.worker_id, WorkerStatus.RUNNING)
        await self.events.publish("worker.started", worker_id=record.worker_id, challenge_id=record.challenge_id)

    async def terminate(self, worker_id: str) -> None:
        await self.repository.set_worker_status(worker_id, WorkerStatus.TERMINATED)
        await self.events.publish("worker.terminated", worker_id=worker_id)
