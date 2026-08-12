import asyncio
from collections.abc import Awaitable, Callable
from ctf_harness.protocol import WorkerAssignment, WorkerReport
from ctf_harness.worker import WorkerRunner

class LocalWorkerScheduler:
    def __init__(self, runner_factory: Callable[[], WorkerRunner], max_workers: int = 3):
        if not 1 <= max_workers <= 3:
            raise ValueError("max_workers must be between 1 and 3")
        self.runner_factory = runner_factory
        self.max_workers = max_workers
        self.semaphore = asyncio.Semaphore(max_workers)
        self.tasks: dict[str, asyncio.Task[WorkerReport]] = {}

    def spawn(self, assignment: WorkerAssignment, report: Callable[[WorkerReport], Awaitable[None]]) -> None:
        if assignment.worker_id in self.tasks:
            raise ValueError(f"duplicate worker id: {assignment.worker_id}")
        async def execute() -> WorkerReport:
            async with self.semaphore:
                return await self.runner_factory().run(assignment, report)
        self.tasks[assignment.worker_id] = asyncio.create_task(execute(), name=assignment.worker_id)

    async def wait_all(self) -> list[WorkerReport]:
        return list(await asyncio.gather(*self.tasks.values()))

    def remove(self, worker_id: str) -> None:
        task = self.tasks.get(worker_id)
        if task is not None and not task.done():
            raise RuntimeError(f"worker is still running: {worker_id}")
        self.tasks.pop(worker_id, None)
