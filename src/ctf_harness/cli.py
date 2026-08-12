import argparse
import asyncio
import json
import os
import tempfile
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable

from ctf_harness.app import build_codex_harness
from ctf_harness.dashboard import serve_dashboard
from ctf_harness.domain import Challenge
from ctf_harness.events import EventBus, ProgressReporter, seek_worker
from ctf_harness.platforms import CTFdPlatformAdapter, MemoryPlatformAdapter
from ctf_harness.scheduler import LocalWorkerScheduler
from ctf_harness.storage import LocalObjectStore, MemoryRunRepository
from ctf_harness.worker import DemoWorkerRunner
from ctf_harness.workflows import CtfRunWorkflow


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def _positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return parsed


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
    submit_flags: bool = False,
    max_wrong_submissions: int = 3,
    progress_mode: str = "human",
    heartbeat_interval_s: float = 15.0,
) -> int:
    """Download the current CTFd snapshot and solve it with real Codex workers."""
    platform = adapter_factory(base_url, token=token, timeout_s=timeout_s)
    harness = build_harness(
        platform=platform,
        runs_root=runs_root,
        worker_model=model,
        max_swarms=max_swarms,
        poll_interval_s=5.0,
        submit_flags=submit_flags,
        max_wrong_submissions=max_wrong_submissions,
        heartbeat_interval_s=heartbeat_interval_s,
    )
    reporter = None
    if hasattr(harness, "events"):
        reporter = ProgressReporter(
            harness.events, runs_root, run_id, console_mode=progress_mode
        )
        await reporter.start()
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
    finally:
        if reporter is not None:
            await reporter.stop()
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
    ctfd_run.add_argument(
        "--submit-flags",
        action="store_true",
        help="submit worker flag candidates to CTFd",
    )
    ctfd_run.add_argument(
        "--max-wrong-submissions",
        type=_positive_int,
        default=3,
        help="stop a worker when this many candidates have been rejected",
    )
    ctfd_run.add_argument("--heartbeat-interval", type=_positive_float, default=15.0)
    progress = ctfd_run.add_mutually_exclusive_group()
    progress.add_argument(
        "--progress", dest="progress_mode", action="store_const", const="human"
    )
    progress.add_argument(
        "--progress-json", dest="progress_mode", action="store_const", const="json"
    )
    progress.add_argument(
        "--quiet", dest="progress_mode", action="store_const", const="quiet"
    )
    ctfd_run.set_defaults(progress_mode="human")

    seek = commands.add_parser("seek", help="follow detailed activity for one worker")
    seek.add_argument("target", choices=("worker",))
    seek.add_argument("worker_number", type=_positive_int)
    seek.add_argument("--runs-root", type=Path, default=Path("runs"))
    seek.add_argument("--run-id")
    seek.add_argument("--no-follow", action="store_true")
    seek.add_argument("--raw", action="store_true", help="print matching raw JSONL events")

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
                submit_flags=args.submit_flags,
                max_wrong_submissions=args.max_wrong_submissions,
                progress_mode=args.progress_mode,
                heartbeat_interval_s=args.heartbeat_interval,
            )
        )
    if args.command == "seek":
        try:
            return seek_worker(
                args.runs_root,
                args.worker_number,
                run_id=args.run_id,
                follow=not args.no_follow,
                raw=args.raw,
                stream=sys.stdout,
            )
        except FileNotFoundError as exc:
            print(str(exc), file=sys.stderr)
            return 1

    if args.command == "dashboard":
        serve_dashboard(args.host, args.port, args.runs_root)
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
