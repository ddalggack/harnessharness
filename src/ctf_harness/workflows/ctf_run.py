from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass

from ctf_harness.domain import Challenge, WorkerProfile
from ctf_harness.domain.models import WorkerRecord
from ctf_harness.events import EventBus
from ctf_harness.main_agent import MainAgentRuntime
from ctf_harness.platforms import PlatformAdapter
from ctf_harness.poller import PlatformPoller, PollEventKind
from ctf_harness.protocol import ReportKind, WorkerReport
from ctf_harness.scheduler import LocalWorkerScheduler
from ctf_harness.storage import LocalObjectStore, MemoryRunRepository


@dataclass(frozen=True, slots=True)
class CtfRunResult:
    run_id: str
    challenge_count: int
    completed_workers: int
    event_count: int


class CtfRunWorkflow:
    def __init__(
        self,
        platform: PlatformAdapter,
        scheduler: LocalWorkerScheduler,
        repository: MemoryRunRepository,
        object_store: LocalObjectStore,
        events: EventBus,
        main: MainAgentRuntime | None = None,
        worker_model: str = "gpt-5.4",
        poll_interval_s: float = 5.0,
    ) -> None:
        self.platform = platform
        self.scheduler = scheduler
        self.repository = repository
        self.object_store = object_store
        self.events = events
        self.main = main or MainAgentRuntime(repository, events)
        self.worker_model = worker_model
        self.poll_interval_s = poll_interval_s

    async def _schedule(self, run_id: str, challenge: Challenge) -> bool:
        worker_id = f"{run_id}-{challenge.id}"
        if worker_id in self.scheduler.tasks or worker_id in self.repository.workers:
            return False
        if len(self.scheduler.tasks) >= self.scheduler.max_workers:
            raise RuntimeError("attempted to start a swarm without an available slot")

        workspace = self.object_store.challenge_workspace(run_id, challenge.id)
        await self.platform.download_challenge(challenge, workspace)
        assignment = await self.main.create_assignment(
            run_id, worker_id, challenge, workspace, self.worker_model
        )
        profile = WorkerProfile(
            assignment.profile,
            f"ddalggack/worker-{assignment.profile}:latest",
        )
        await self.main.register(
            WorkerRecord(worker_id, challenge.id, profile, assignment.model)
        )
        self.scheduler.spawn(assignment, self.main.handle_report)
        return True

    async def _reap_finished(self, run_id: str) -> int:
        completed = 0
        for worker_id, task in list(self.scheduler.tasks.items()):
            if not task.done():
                continue
            try:
                task.result()
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                record = self.repository.workers[worker_id]
                await self.main.handle_report(
                    WorkerReport(
                        run_id,
                        worker_id,
                        record.challenge_id,
                        ReportKind.FAILED,
                        f"worker runtime failed: {type(exc).__name__}: {exc}",
                    )
                )
                completed += 1
            else:
                completed += 1
            await self.main.terminate(worker_id)
            self.scheduler.remove(worker_id)
        return completed

    async def _cancel_active(self) -> None:
        tasks = list(self.scheduler.tasks.items())
        for _, task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*(task for _, task in tasks), return_exceptions=True)
        for worker_id, _ in tasks:
            await self.main.terminate(worker_id)
            self.scheduler.remove(worker_id)

    async def run(self, run_id: str) -> CtfRunResult:
        """Process the current unsolved snapshot using an explicit pending queue."""
        poller = PlatformPoller(self.platform, interval_s=self.poll_interval_s)
        completed = 0
        await self.main.start()
        try:
            await poller.start()
            challenges = [
                challenge
                for challenge in poller.known_challenges
                if challenge.id not in poller.known_solved_ids
            ]
            pending = deque(challenges)
            await self.events.publish(
                "run.started", run_id=run_id, challenge_count=len(challenges)
            )

            while pending or self.scheduler.tasks:
                while pending and len(self.scheduler.tasks) < self.scheduler.max_workers:
                    await self._schedule(run_id, pending.popleft())

                if self.scheduler.tasks:
                    await asyncio.wait(
                        tuple(self.scheduler.tasks.values()),
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    completed += await self._reap_finished(run_id)

            await self.events.publish("run.completed", run_id=run_id, workers=completed)
            return CtfRunResult(
                run_id, len(challenges), completed, len(self.events.history)
            )
        finally:
            await self._cancel_active()
            await poller.stop()
            await self.main.stop()

    async def run_live(self, run_id: str, stop: asyncio.Event) -> CtfRunResult:
        """Continuously consume poll events while keeping at most three active swarms."""
        poller = PlatformPoller(self.platform, interval_s=self.poll_interval_s)
        pending: deque[Challenge] = deque()
        accepted_ids: set[str] = set()
        completed = 0
        await self.main.start()
        try:
            await poller.start()
            for challenge in poller.known_challenges:
                if challenge.id not in poller.known_solved_ids:
                    pending.append(challenge)
                    accepted_ids.add(challenge.id)

            await self.events.publish("run.started", run_id=run_id, mode="live")
            while not stop.is_set():
                while pending and len(self.scheduler.tasks) < self.scheduler.max_workers:
                    await self._schedule(run_id, pending.popleft())

                event = await poller.get_event(
                    timeout=min(self.poll_interval_s, 0.1)
                )
                events = ([event] if event is not None else []) + poller.drain_events()
                for item in events:
                    if (
                        item.kind is PollEventKind.NEW_CHALLENGE
                        and item.challenge is not None
                        and item.challenge_id not in accepted_ids
                    ):
                        pending.append(item.challenge)
                        accepted_ids.add(item.challenge_id)
                        await self.events.publish(
                            "platform.challenge_discovered",
                            run_id=run_id,
                            challenge_id=item.challenge_id,
                        )
                    elif item.kind is PollEventKind.CHALLENGE_SOLVED:
                        pending = deque(
                            challenge
                            for challenge in pending
                            if challenge.id != item.challenge_id
                        )
                        worker_id = f"{run_id}-{item.challenge_id}"
                        task = self.scheduler.tasks.get(worker_id)
                        if task is not None:
                            task.cancel()
                            await asyncio.gather(task, return_exceptions=True)
                            await self.main.terminate(worker_id)
                            self.scheduler.remove(worker_id)
                            await self.events.publish(
                                "swarm.cancelled_external_solve",
                                worker_id=worker_id,
                                challenge_id=item.challenge_id,
                            )

                completed += await self._reap_finished(run_id)

            await self.events.publish("run.stopped", run_id=run_id)
            return CtfRunResult(
                run_id, len(accepted_ids), completed, len(self.events.history)
            )
        finally:
            await self._cancel_active()
            await poller.stop()
            await self.main.stop()
