from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ctf_harness.events import EventBus
from ctf_harness.main_agent import MainAgentRuntime
from ctf_harness.platforms import PlatformAdapter
from ctf_harness.scheduler import LocalWorkerScheduler
from ctf_harness.storage import LocalObjectStore, MemoryRunRepository
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
    auto_submit_flags: bool = False,
) -> CodexHarness:
    """Wire deterministic run coordination to per-challenge Codex workers."""
    root = runs_root.resolve()
    repository = MemoryRunRepository()
    events = EventBus()
    main = MainAgentRuntime(repository, events)
    scheduler = LocalWorkerScheduler(CodexWorkerRunner, max_workers=max_swarms)
    workflow = CtfRunWorkflow(
        platform,
        scheduler,
        repository,
        LocalObjectStore(root),
        events,
        main=main,
        worker_model=worker_model,
        poll_interval_s=poll_interval_s,
        auto_submit_flags=auto_submit_flags,
    )
    return CodexHarness(workflow, scheduler, repository, events)
