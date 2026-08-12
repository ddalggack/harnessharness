import argparse
import asyncio
import json
import os
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable

from ctf_harness.app import build_codex_harness
from ctf_harness.dashboard import serve_dashboard
from ctf_harness.domain import Challenge
from ctf_harness.events import EventBus
from ctf_harness.platforms import CTFdPlatformAdapter, MemoryPlatformAdapter
from ctf_harness.scheduler import LocalWorkerScheduler
from ctf_harness.storage import LocalObjectStore, MemoryRunRepository
from ctf_harness.worker import DemoWorkerRunner
from ctf_harness.workflows import CtfRunWorkflow


async def _smoke(workers: int) -> int:
    platform = MemoryPlatformAdapter(
        [
            Challenge("pwn-1", "baby-bof", "pwn"),
            Challenge("rev-1", "tiny-rev", "rev"),
            Challenge("web-1", "local-web", "web"),
        ]
    )
    repository, events = MemoryRunRepository(), EventBus()
    scheduler = LocalWorkerScheduler(DemoWorkerRunner, max_workers=workers)
    with tempfile.TemporaryDirectory(prefix="ddalggack-") as tmp:
        result = await CtfRunWorkflow(
            platform,
            scheduler,
            repository,
            LocalObjectStore(Path(tmp)),
            events,
        ).run("smoke-run")
    print(
        json.dumps(
            {
                "run_id": result.run_id,
                "challenges": result.challenge_count,
                "completed_workers": result.completed_workers,
                "active_workers_after_cleanup": len(scheduler.tasks),
                "events": result.event_count,
                "worker_states": {
                    key: value.status.value for key, value in repository.workers.items()
                },
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


async def _codex_demo(
    model: str,
    runs_root: Path,
    build_harness: Callable[..., Any] = build_codex_harness,
) -> int:
    """Call one real Codex worker and print its in-memory structured reports."""
    platform = MemoryPlatformAdapter(
        [
            Challenge(
                "demo-1",
                "Codex API connectivity demo",
                "misc",
                (
                    "This is an API connectivity check. Do not inspect unrelated files or "
                    "services. Return a completed structured report stating that the Codex "
                    "worker received this challenge. No flag is required."
                ),
            )
        ]
    )
    harness = build_harness(
        platform=platform,
        runs_root=runs_root,
        worker_model=model,
        max_swarms=1,
        poll_interval_s=5.0,
    )
    result = await harness.workflow.run("codex-demo")
    workers = []
    for record in harness.repository.workers.values():
        workers.append(
            {
                "worker_id": record.worker_id,
                "challenge_id": record.challenge_id,
                "status": record.status.value,
                "reports": [asdict(report) for report in record.reports],
            }
        )
    print(
        json.dumps(
            {
                "run_id": result.run_id,
                "challenges": result.challenge_count,
                "completed_workers": result.completed_workers,
                "workers": workers,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if result.completed_workers == result.challenge_count else 1


def _result_payload(result: Any, harness: Any) -> dict[str, Any]:
    return {
        "run_id": result.run_id,
        "challenges": result.challenge_count,
        "completed_workers": result.completed_workers,
        "active_workers_after_cleanup": len(harness.scheduler.tasks),
        "workers": [
            {
                "worker_id": record.worker_id,
                "challenge_id": record.challenge_id,
                "status": record.status.value,
                "reports": [asdict(report) for report in record.reports],
            }
            for record in harness.repository.workers.values()
        ],
    }


async def _ctfd_run(
    base_url: str,
    token: str | None,
    model: str,
    runs_root: Path,
    max_swarms: int,
    run_id: str,
    timeout_s: float,
    adapter_factory: Callable[..., Any] = CTFdPlatformAdapter,
    build_harness: Callable[..., Any] = build_codex_harness,
) -> int:
    """Download the current CTFd snapshot and solve it with real Codex workers."""
    platform = adapter_factory(base_url, token=token, timeout_s=timeout_s)
    harness = build_harness(
        platform=platform,
        runs_root=runs_root,
        worker_model=model,
        max_swarms=max_swarms,
        poll_interval_s=5.0,
    )
    try:
        result = await harness.workflow.run(run_id)
    except Exception as exc:
        print(
            json.dumps(
                {"run_id": run_id, "error": f"{type(exc).__name__}: {exc}"},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1
    print(json.dumps(_result_payload(result, harness), ensure_ascii=False, indent=2))
    return 0 if result.completed_workers == result.challenge_count else 1


def main() -> int:
    parser = argparse.ArgumentParser(prog="ddalggack")
    commands = parser.add_subparsers(dest="command", required=True)

    smoke = commands.add_parser("smoke", help="run the offline orchestration demo")
    smoke.add_argument("--workers", type=int, choices=range(1, 4), default=3)

    codex_demo = commands.add_parser(
        "codex-demo", help="call one real Codex worker using the configured Codex login"
    )
    codex_demo.add_argument("--model", default="gpt-5.4")
    codex_demo.add_argument("--runs-root", type=Path, default=Path("runs"))

    ctfd_run = commands.add_parser(
        "ctfd-run", help="download a CTFd snapshot and run real Codex workers"
    )
    ctfd_run.add_argument("--url", required=True)
    ctfd_run.add_argument("--token", default=os.environ.get("CTFD_TOKEN"))
    ctfd_run.add_argument("--model", default="gpt-5.4")
    ctfd_run.add_argument("--runs-root", type=Path, default=Path("runs"))
    ctfd_run.add_argument("--max-swarms", type=int, choices=range(1, 4), default=3)
    ctfd_run.add_argument("--run-id", default="ctfd-run")
    ctfd_run.add_argument("--timeout", type=float, default=30.0)

    dashboard = commands.add_parser("dashboard", help="serve the local web dashboard")
    dashboard.add_argument("--host", default="127.0.0.1")
    dashboard.add_argument("--port", type=int, default=8788)
    dashboard.add_argument("--runs-root", type=Path, default=Path("runs"))

    args = parser.parse_args()
    if args.command == "smoke":
        return asyncio.run(_smoke(args.workers))
    if args.command == "codex-demo":
        return asyncio.run(_codex_demo(args.model, args.runs_root))
    if args.command == "ctfd-run":
        return asyncio.run(
            _ctfd_run(
                args.url,
                args.token,
                args.model,
                args.runs_root,
                args.max_swarms,
                args.run_id,
                args.timeout,
            )
        )
    if args.command == "dashboard":
        serve_dashboard(args.host, args.port, args.runs_root)
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
