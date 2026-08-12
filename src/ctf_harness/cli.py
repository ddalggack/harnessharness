import argparse
import asyncio
import json
import tempfile
from pathlib import Path
from ctf_harness.domain import Challenge
from ctf_harness.events import EventBus
from ctf_harness.platforms import MemoryPlatformAdapter
from ctf_harness.scheduler import LocalWorkerScheduler
from ctf_harness.storage import LocalObjectStore, MemoryRunRepository
from ctf_harness.worker import DemoWorkerRunner
from ctf_harness.workflows import CtfRunWorkflow

async def _smoke(workers: int) -> int:
    platform = MemoryPlatformAdapter([Challenge("pwn-1", "baby-bof", "pwn"), Challenge("rev-1", "tiny-rev", "rev"), Challenge("web-1", "local-web", "web")])
    repository, events = MemoryRunRepository(), EventBus()
    scheduler = LocalWorkerScheduler(DemoWorkerRunner, max_workers=workers)
    with tempfile.TemporaryDirectory(prefix="ddalggack-") as tmp:
        result = await CtfRunWorkflow(platform, scheduler, repository, LocalObjectStore(Path(tmp)), events).run("smoke-run")
    print(json.dumps({"run_id": result.run_id, "challenges": result.challenge_count, "completed_workers": result.completed_workers, "active_workers_after_cleanup": len(scheduler.tasks), "events": result.event_count, "worker_states": {k: v.status.value for k, v in repository.workers.items()}}, ensure_ascii=False, indent=2))
    return 0

def main() -> int:
    parser = argparse.ArgumentParser(prog="ddalggack")
    commands = parser.add_subparsers(dest="command", required=True)
    smoke = commands.add_parser("smoke")
    smoke.add_argument("--workers", type=int, default=3)
    args = parser.parse_args()
    return asyncio.run(_smoke(args.workers)) if args.command == "smoke" else 2

if __name__ == "__main__":
    raise SystemExit(main())
