import asyncio
import tempfile
import unittest
from pathlib import Path
from ctf_harness.domain import Challenge
from ctf_harness.events import EventBus
from ctf_harness.platforms import MemoryPlatformAdapter
from ctf_harness.scheduler import LocalWorkerScheduler
from ctf_harness.storage import LocalObjectStore, MemoryRunRepository
from ctf_harness.worker import DemoWorkerRunner
from ctf_harness.workflows import CtfRunWorkflow

class WorkflowTests(unittest.TestCase):
    def test_display_worker_slot_is_reused_after_completion(self):
        async def scenario():
            challenges = [Challenge("one", "One", "pwn"), Challenge("two", "Two", "rev")]
            repo, events = MemoryRunRepository(), EventBus()
            scheduler = LocalWorkerScheduler(DemoWorkerRunner, 1)
            with tempfile.TemporaryDirectory() as tmp:
                await CtfRunWorkflow(
                    MemoryPlatformAdapter(challenges),
                    scheduler,
                    repo,
                    LocalObjectStore(Path(tmp)),
                    events,
                ).run("run-slots")

            started = [event for event in events.history if event.type == "worker.started"]
            self.assertEqual([event.payload["worker_number"] for event in started], [1, 1])
            self.assertEqual([event.payload["challenge_title"] for event in started], ["One", "Two"])

        asyncio.run(scenario())

    def test_workers_complete_and_are_removed(self):
        async def scenario():
            repo, events = MemoryRunRepository(), EventBus()
            scheduler = LocalWorkerScheduler(DemoWorkerRunner, 2)
            with tempfile.TemporaryDirectory() as tmp:
                result = await CtfRunWorkflow(MemoryPlatformAdapter([Challenge("one", "One", "pwn"), Challenge("two", "Two", "rev")]), scheduler, repo, LocalObjectStore(Path(tmp)), events).run("run-1")
            self.assertEqual((result.challenge_count, result.completed_workers), (2, 2))
            self.assertEqual(scheduler.tasks, {})
            self.assertEqual({x.status.value for x in repo.workers.values()}, {"completed"})
        asyncio.run(scenario())

    def test_failed_worker_keeps_failed_status_and_is_not_counted_completed(self):
        async def scenario():
            from ctf_harness.protocol import ReportKind, WorkerReport

            class FailedRunner:
                async def run(self, assignment, report):
                    failed = WorkerReport(
                        assignment.run_id,
                        assignment.worker_id,
                        assignment.challenge_id,
                        ReportKind.FAILED,
                        "terminal failure",
                        ("evidence.log",),
                    )
                    await report(failed)
                    return failed

            repo, events = MemoryRunRepository(), EventBus()
            scheduler = LocalWorkerScheduler(FailedRunner, 1)
            with tempfile.TemporaryDirectory() as tmp:
                result = await CtfRunWorkflow(
                    MemoryPlatformAdapter([Challenge("one", "One", "pwn")]),
                    scheduler,
                    repo,
                    LocalObjectStore(Path(tmp)),
                    events,
                ).run("run-failed")

            self.assertEqual(result.completed_workers, 0)
            self.assertEqual(repo.workers["run-failed-one"].status.value, "failed")
            self.assertEqual(scheduler.tasks, {})

        asyncio.run(scenario())

    def test_cleanup_does_not_overwrite_a_finished_worker_status(self):
        async def scenario():
            repo, events = MemoryRunRepository(), EventBus()
            scheduler = LocalWorkerScheduler(DemoWorkerRunner, 1)
            challenge = Challenge("one", "One", "pwn")
            with tempfile.TemporaryDirectory() as tmp:
                workflow = CtfRunWorkflow(
                    MemoryPlatformAdapter([challenge]),
                    scheduler,
                    repo,
                    LocalObjectStore(Path(tmp)),
                    events,
                )
                await workflow._schedule("run-cleanup", challenge)
                await scheduler.wait_all()
                await workflow._cancel_active("run-cleanup")

            self.assertEqual(repo.workers["run-cleanup-one"].status.value, "completed")
            self.assertEqual(scheduler.tasks, {})

        asyncio.run(scenario())

    def test_cleanup_records_an_already_failed_task(self):
        async def scenario():
            class CrashingRunner:
                async def run(self, assignment, report):
                    raise RuntimeError("boom")

            repo, events = MemoryRunRepository(), EventBus()
            scheduler = LocalWorkerScheduler(CrashingRunner, 1)
            challenge = Challenge("one", "One", "pwn")
            with tempfile.TemporaryDirectory() as tmp:
                workflow = CtfRunWorkflow(
                    MemoryPlatformAdapter([challenge]),
                    scheduler,
                    repo,
                    LocalObjectStore(Path(tmp)),
                    events,
                )
                await workflow._schedule("run-cleanup", challenge)
                await asyncio.gather(*scheduler.tasks.values(), return_exceptions=True)
                await workflow._cancel_active("run-cleanup")

            record = repo.workers["run-cleanup-one"]
            self.assertEqual(record.status.value, "failed")
            self.assertEqual(record.reports[-1].kind.value, "failed")
            self.assertIn("RuntimeError: boom", record.reports[-1].summary)
            self.assertEqual(scheduler.tasks, {})

        asyncio.run(scenario())

    def test_invalid_concurrency(self):
        with self.assertRaises(ValueError):
            LocalWorkerScheduler(DemoWorkerRunner, 0)
        with self.assertRaises(ValueError):
            LocalWorkerScheduler(DemoWorkerRunner, 4)

    def test_scheduler_never_runs_more_than_three_swarms(self):
        async def scenario():
            running = 0
            peak = 0
            release = asyncio.Event()

            class TrackingRunner:
                async def run(self, assignment, report):
                    nonlocal running, peak
                    running += 1
                    peak = max(peak, running)
                    if peak == 3:
                        release.set()
                    await release.wait()
                    await asyncio.sleep(0)
                    running -= 1
                    result = __import__("ctf_harness.protocol", fromlist=["WorkerReport"]).WorkerReport(
                        assignment.run_id,
                        assignment.worker_id,
                        assignment.challenge_id,
                        __import__("ctf_harness.protocol", fromlist=["ReportKind"]).ReportKind.COMPLETED,
                        "done",
                    )
                    await report(result)
                    return result

            async def report(_item):
                return None

            from ctf_harness.protocol import WorkerAssignment

            scheduler = LocalWorkerScheduler(TrackingRunner, 3)
            for index in range(6):
                worker_id = f"worker-{index}"
                scheduler.spawn(
                    WorkerAssignment(
                        run_id="run",
                        worker_id=worker_id,
                        challenge_id=str(index),
                        challenge_title=f"Challenge {index}",
                        challenge_category="general",
                        challenge_description="",
                        workspace_uri=".",
                        model="gpt-5.4-mini",
                    ),
                    report,
                )
            await scheduler.wait_all()
            self.assertEqual(peak, 3)

        asyncio.run(scenario())

    def test_workflow_keeps_excess_challenges_pending_until_a_slot_opens(self):
        async def scenario():
            release = asyncio.Event()
            started: list[str] = []

            class BlockingRunner:
                async def run(self, assignment, report):
                    from ctf_harness.protocol import ReportKind, WorkerReport

                    started.append(assignment.challenge_id)
                    await release.wait()
                    result = WorkerReport(
                        assignment.run_id,
                        assignment.worker_id,
                        assignment.challenge_id,
                        ReportKind.COMPLETED,
                        "done",
                    )
                    await report(result)
                    return result

            challenges = [Challenge(str(index), f"Challenge {index}", "pwn") for index in range(5)]
            repo, events = MemoryRunRepository(), EventBus()
            scheduler = LocalWorkerScheduler(BlockingRunner, 3)
            with tempfile.TemporaryDirectory() as tmp:
                workflow = CtfRunWorkflow(
                    MemoryPlatformAdapter(challenges),
                    scheduler,
                    repo,
                    LocalObjectStore(Path(tmp)),
                    events,
                )
                task = asyncio.create_task(workflow.run("run-pending"))
                for _ in range(100):
                    if len(started) == 3:
                        break
                    await asyncio.sleep(0.01)

                self.assertEqual(len(scheduler.tasks), 3)
                self.assertEqual(len(repo.workers), 3)
                release.set()
                result = await task

            self.assertEqual(result.completed_workers, 5)
            self.assertEqual(len(repo.workers), 5)
            self.assertEqual(scheduler.tasks, {})

        asyncio.run(scenario())

    def test_already_solved_challenges_are_not_scheduled(self):
        async def scenario():
            platform = MemoryPlatformAdapter(
                [Challenge("one", "One", "pwn"), Challenge("two", "Two", "rev")]
            )
            platform.mark_solved("one")
            repo, events = MemoryRunRepository(), EventBus()
            scheduler = LocalWorkerScheduler(DemoWorkerRunner, 3)
            with tempfile.TemporaryDirectory() as tmp:
                result = await CtfRunWorkflow(
                    platform,
                    scheduler,
                    repo,
                    LocalObjectStore(Path(tmp)),
                    events,
                ).run("run-1")
            self.assertEqual(result.challenge_count, 1)
            self.assertEqual(set(repo.workers), {"run-1-two"})

        asyncio.run(scenario())

    def test_live_workflow_schedules_a_newly_polled_challenge(self):
        async def scenario():
            platform = MemoryPlatformAdapter([])
            repo, events = MemoryRunRepository(), EventBus()
            scheduler = LocalWorkerScheduler(DemoWorkerRunner, 3)
            stop = asyncio.Event()
            with tempfile.TemporaryDirectory() as tmp:
                workflow = CtfRunWorkflow(
                    platform,
                    scheduler,
                    repo,
                    LocalObjectStore(Path(tmp)),
                    events,
                    poll_interval_s=0.01,
                )
                task = asyncio.create_task(workflow.run_live("run-live", stop))
                await asyncio.sleep(0.02)
                platform.add_challenge(Challenge("late", "Late", "pwn"))
                for _ in range(100):
                    record = repo.workers.get("run-live-late")
                    if record and record.status.value == "terminated":
                        break
                    await asyncio.sleep(0.01)
                stop.set()
                result = await task

            self.assertEqual(result.challenge_count, 1)
            self.assertEqual(result.completed_workers, 1)
            self.assertEqual(scheduler.tasks, {})

        asyncio.run(scenario())

    def test_live_workflow_cancels_a_swarm_when_platform_marks_it_solved(self):
        async def scenario():
            class BlockingRunner:
                async def run(self, assignment, report):
                    await asyncio.Event().wait()

            platform = MemoryPlatformAdapter([Challenge("one", "One", "pwn")])
            repo, events = MemoryRunRepository(), EventBus()
            scheduler = LocalWorkerScheduler(BlockingRunner, 3)
            stop = asyncio.Event()
            with tempfile.TemporaryDirectory() as tmp:
                workflow = CtfRunWorkflow(
                    platform,
                    scheduler,
                    repo,
                    LocalObjectStore(Path(tmp)),
                    events,
                    poll_interval_s=0.01,
                )
                task = asyncio.create_task(workflow.run_live("run-live", stop))
                for _ in range(100):
                    if "run-live-one" in scheduler.tasks:
                        break
                    await asyncio.sleep(0.01)
                platform.mark_solved("one")
                for _ in range(100):
                    if not scheduler.tasks:
                        break
                    await asyncio.sleep(0.01)
                stop.set()
                await task

            self.assertEqual(scheduler.tasks, {})
            self.assertEqual(repo.workers["run-live-one"].status.value, "terminated")

        asyncio.run(scenario())
