import asyncio
from collections.abc import Awaitable, Callable
from typing import Protocol
from ctf_harness.protocol import ReportKind, WorkerAssignment, WorkerReport

ReportCallback = Callable[[WorkerReport], Awaitable[None]]
class WorkerRunner(Protocol):
    async def run(self, assignment: WorkerAssignment, report: ReportCallback) -> WorkerReport: ...

class DemoWorkerRunner:
    """Lifecycle probe only; never claims a real CTF solution."""
    async def run(self, assignment: WorkerAssignment, report: ReportCallback) -> WorkerReport:
        await asyncio.sleep(0)
        await report(WorkerReport(assignment.run_id, assignment.worker_id, assignment.challenge_id, ReportKind.CHECKPOINT, "workspace inspected; demo worker ready"))
        await asyncio.sleep(0)
        completed = WorkerReport(assignment.run_id, assignment.worker_id, assignment.challenge_id, ReportKind.COMPLETED, "demo lifecycle completed; no real CTF solution attempted", ("challenge.json",))
        await report(completed)
        return completed
