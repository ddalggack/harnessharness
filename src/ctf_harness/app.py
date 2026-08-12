from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ctf_harness.events import EventBus
from ctf_harness.main_agent import CodexCoordinatorBackend, MainAgentRuntime
from ctf_harness.platforms import PlatformAdapter
from ctf_harness.scheduler import LocalWorkerScheduler
from ctf_harness.storage import LocalObjectStore, MemoryRunRepository
from ctf_harness.worker import CodexWorkerRunner
from ctf_harness.workflows import CtfRunWorkflow


@dataclass(frozen=True, slots=True)
class CodexHarness:
    workflow: CtfRunWorkflow
    coordinator: CodexCoordinatorBackend
    scheduler: LocalWorkerScheduler
    repository: MemoryRunRepository
    events: EventBus


def build_codex_harness(
    platform: PlatformAdapter,
    runs_root: Path,
    coordinator_model: str = "gpt-5.4",
    worker_model: str = "gpt-5.4",
    max_swarms: int = 3,
    poll_interval_s: float = 5.0,
) -> CodexHarness:
    """Wire the real Codex SDK coordinator/worker harness around a platform adapter."""
    root = runs_root.resolve()
    repository = MemoryRunRepository()
    events = EventBus()
    coordinator = CodexCoordinatorBackend(coordinator_model, cwd=root)
    main = MainAgentRuntime(repository, events, coordinator)
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
    )
    return CodexHarness(workflow, coordinator, scheduler, repository, events)