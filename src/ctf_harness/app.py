from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ctf_harness.events import EventBus
from ctf_harness.main_agent import MainAgentRuntime
from ctf_harness.platforms import PlatformAdapter
from ctf_harness.scheduler import LocalWorkerScheduler
from ctf_harness.storage import LocalObjectStore, MemoryRunRepository
from ctf_harness.submissions import SubmissionBroker
from ctf_harness.worker import CodexWorkerRunner
from ctf_harness.workflows import CtfRunWorkflow


@dataclass(frozen=True, slots=True)
class CodexHarness:
    workflow: CtfRunWorkflow
    scheduler: LocalWorkerScheduler
    repository: MemoryRunRepository
    events: EventBus


def build_codex_harness(
    platform: PlatformAdapter,
    runs_root: Path,
    worker_model: str = "gpt-5.4",
    max_swarms: int = 3,
    poll_interval_s: float = 5.0,
    submit_flags: bool = False,
    max_wrong_submissions: int = 3,
    heartbeat_interval_s: float = 15.0,
) -> CodexHarness:
    """Wire deterministic run coordination to per-challenge Codex workers."""
    root = runs_root.resolve()
    repository = MemoryRunRepository()
    events = EventBus()
    broker = (
        SubmissionBroker(platform, max_wrong_submissions=max_wrong_submissions)
        if submit_flags
        else None
    )
    main = MainAgentRuntime(repository, events, submission_broker=broker)
    scheduler = LocalWorkerScheduler(
        lambda: CodexWorkerRunner(
            activity=main.handle_activity,
            heartbeat_interval_s=heartbeat_interval_s,
        ),
        max_workers=max_swarms,
    )
    workflow = CtfRunWorkflow(
        platform,
        scheduler,
        repository,
        LocalObjectStore(root),
        events,
        main=main,
        worker_model=worker_model,
        poll_interval_s=poll_interval_s,
    )
    return CodexHarness(workflow, scheduler, repository, events)
