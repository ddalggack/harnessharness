from __future__ import annotations

from pathlib import Path

from ctf_harness.domain import Challenge
from ctf_harness.domain.models import WorkerRecord, WorkerStatus
from ctf_harness.events import EventBus
from ctf_harness.protocol import ReportKind, WorkerAssignment, WorkerReport
from ctf_harness.storage import MemoryRunRepository
from ctf_harness.submissions import SubmissionBroker, SubmissionStatus
from ctf_harness.worker.runner import ReportDecision


class MainAgentRuntime:
    """Deterministic run coordinator that never supervises worker tool actions."""

    def __init__(
        self,
        repository: MemoryRunRepository,
        events: EventBus,
        submission_broker: SubmissionBroker | None = None,
    ) -> None:
        self.repository = repository
        self.events = events
        self.submission_broker = submission_broker
        self._worker_metadata: dict[str, dict[str, object]] = {}

    async def create_assignment(
        self,
        run_id: str,
        worker_id: str,
        challenge: Challenge,
        workspace: Path,
        worker_model: str,
        worker_number: int = 0,
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
            worker_number=worker_number,
            host=challenge.host,
            port=challenge.port,
        )

    async def handle_report(self, report: WorkerReport) -> ReportDecision | None:
        metadata = self._worker_metadata.get(report.worker_id, {})
        await self.repository.append_report(report.worker_id, report)
        await self.events.publish(
            "worker.reported",
            worker_id=report.worker_id,
            challenge_id=report.challenge_id,
            kind=report.kind.value,
            summary=report.summary,
            artifacts=report.artifacts,
            flag_candidate=report.flag_candidate,
            **metadata,
        )
        if report.kind is ReportKind.FLAG_CANDIDATE:
            return await self._handle_candidate(report)
        if report.kind is ReportKind.COMPLETED:
            await self.repository.set_worker_status(
                report.worker_id, WorkerStatus.COMPLETED
            )
        elif report.kind is ReportKind.FAILED:
            await self.repository.set_worker_status(report.worker_id, WorkerStatus.FAILED)
        return None

    async def _handle_candidate(self, report: WorkerReport) -> ReportDecision:
        candidate = (report.flag_candidate or "").strip()
        metadata = self._worker_metadata.get(report.worker_id, {})
        if self.submission_broker is None:
            await self.events.publish(
                "submission.disabled",
                worker_id=report.worker_id,
                challenge_id=report.challenge_id,
                candidate=candidate,
                summary="flag candidate found; automatic submission disabled",
                **metadata,
            )
            return ReportDecision(terminal=True, message="automatic submission disabled")

        await self.events.publish(
            "submission.started",
            worker_id=report.worker_id,
            challenge_id=report.challenge_id,
            candidate=candidate,
            summary="submitting flag candidate",
            **metadata,
        )
        result = await self.submission_broker.submit(report.challenge_id, candidate)
        await self.events.publish(
            f"submission.{result.status.value}",
            worker_id=report.worker_id,
            challenge_id=report.challenge_id,
            candidate=candidate,
            summary=result.message or f"flag {result.status.value}",
            **metadata,
        )
        if result.status is SubmissionStatus.ACCEPTED:
            return ReportDecision(accepted=True, terminal=True, message="flag accepted")
        if result.status in {SubmissionStatus.REJECTED, SubmissionStatus.DUPLICATE}:
            return ReportDecision(accepted=False, message=result.message)
        return ReportDecision(accepted=False, terminal=True, message=result.message)

    async def handle_activity(self, event_type: str, **payload: object) -> None:
        await self.events.publish(event_type, **payload)

    async def register(
        self,
        record: WorkerRecord,
        *,
        worker_number: int = 0,
        challenge_title: str = "",
        challenge_category: str = "",
    ) -> None:
        self._worker_metadata[record.worker_id] = {
            "worker_number": worker_number,
            "challenge_title": challenge_title,
            "challenge_category": challenge_category,
        }
        await self.repository.add_worker(record)
        await self.repository.set_worker_status(record.worker_id, WorkerStatus.RUNNING)
        await self.events.publish(
            "worker.started",
            worker_id=record.worker_id,
            challenge_id=record.challenge_id,
            worker_number=worker_number,
            challenge_title=challenge_title,
            challenge_category=challenge_category,
        )

    async def terminate(self, worker_id: str) -> None:
        await self.repository.set_worker_status(worker_id, WorkerStatus.TERMINATED)
        await self.events.publish("worker.terminated", worker_id=worker_id)
