from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ctf_harness.domain import Challenge


_SOLVER_NAMES = ("solver.py", "exploit.py", "solve.py", "solution.py")
_SOLVER_SUFFIXES = (".py", ".sage", ".sh", ".js", ".ts", ".rb", ".go", ".rs", ".c", ".cpp")


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip(".-")
    return cleaned or "challenge"


def find_solver(workspace: Path, artifacts: tuple[str, ...] = ()) -> Path | None:
    root = workspace.resolve()
    candidates: list[Path] = []
    for artifact in artifacts:
        candidate = (root / artifact).resolve()
        if candidate == root or root not in candidate.parents:
            continue
        candidates.append(candidate)
    for name in _SOLVER_NAMES:
        candidates.extend(root.rglob(name))
    candidates.extend(
        path
        for path in root.rglob("*")
        if path.suffix.lower() in _SOLVER_SUFFIXES
        and any(word in path.stem.lower() for word in ("solve", "solver", "exploit"))
    )
    for candidate in candidates:
        try:
            if candidate.is_file() and candidate.stat().st_size > 0:
                return candidate
        except OSError:
            continue
    return None


class SolvedDatabase:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def archive_run(
        self,
        run_id: str,
        challenges: list[Challenge],
        records: list[Any],
        events: list[Any],
        runs_root: Path,
    ) -> list[str]:
        challenge_by_id = {challenge.id: challenge for challenge in challenges}
        archived: list[str] = []
        for record in records:
            if record.status.value != "completed":
                continue
            challenge = challenge_by_id.get(record.challenge_id)
            if challenge is None:
                continue
            workspace = runs_root.resolve() / run_id / "challenges" / challenge.id
            artifacts = tuple(
                artifact
                for report in record.reports
                for artifact in report.artifacts
            )
            solver = find_solver(workspace, artifacts)
            if solver is None:
                continue

            record_id = _slug(f"{challenge.category}-{challenge.id}")
            entry = self.root / record_id
            entry.mkdir(parents=True, exist_ok=True)
            previous = self._read_status(entry)
            previous_solver = previous.get("solver_file")
            if (
                isinstance(previous_solver, str)
                and Path(previous_solver).name == previous_solver
                and previous_solver != solver.name
            ):
                try:
                    (entry / previous_solver).unlink()
                except FileNotFoundError:
                    pass
            try:
                (entry / "write-up.md").unlink()
            except FileNotFoundError:
                pass
            solver_name = solver.name if solver.suffix.lower() in _SOLVER_SUFFIXES else "solver.py"
            shutil.copy2(solver, entry / solver_name)

            challenge_events = [
                asdict(event)
                for event in events
                if event.payload.get("challenge_id") == challenge.id
                or event.payload.get("worker_id") == record.worker_id
            ]
            (entry / "event.json").write_text(
                json.dumps(challenge_events, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            accepted = next(
                (
                    event.payload.get("candidate")
                    for event in events
                    if event.type == "submission.accepted"
                    and event.payload.get("worker_id") == record.worker_id
                ),
                None,
            )
            status = {
                "record_id": record_id,
                "run_id": run_id,
                "worker_id": record.worker_id,
                "challenge": asdict(challenge),
                "model": record.model,
                "solver_file": solver_name,
                "verification": "accepted" if accepted else "worker-completed",
                "accepted_flag": accepted,
                "reports": [asdict(report) for report in record.reports],
                "archived_at": datetime.now(timezone.utc).isoformat(),
                "writeup": {"status": "not-generated"},
            }
            self._write_status(entry, status)
            archived.append(record_id)
        return archived

    def list_records(self) -> list[dict[str, Any]]:
        if not self.root.exists():
            return []
        records = []
        for entry in self.root.iterdir():
            if not entry.is_dir():
                continue
            status = self._read_status(entry)
            if not status:
                continue
            challenge = status.get("challenge", {})
            records.append({
                "id": status.get("record_id", entry.name),
                "name": challenge.get("title", challenge.get("id", entry.name)),
                "category": challenge.get("category", ""),
                "model": status.get("model", ""),
                "verification": status.get("verification", ""),
                "solverFile": status.get("solver_file", ""),
                "archivedAt": status.get("archived_at"),
                "writeupStatus": status.get("writeup", {}).get("status", "not-generated"),
                "hasWriteup": (entry / "write-up.md").is_file(),
            })
        return sorted(records, key=lambda item: item.get("archivedAt") or "", reverse=True)

    def entry(self, record_id: str) -> Path:
        if record_id != _slug(record_id):
            raise ValueError("올바르지 않은 solved-db 레코드 ID입니다.")
        entry = (self.root / record_id).resolve()
        if entry.parent != self.root or not (entry / "status.json").is_file():
            raise FileNotFoundError("solved-db 레코드를 찾을 수 없습니다.")
        return entry

    def delete(self, record_id: str) -> None:
        shutil.rmtree(self.entry(record_id))

    def clear(self) -> None:
        if self.root.exists():
            shutil.rmtree(self.root)

    def mark_writeup(self, record_id: str, status_value: str, error: str | None = None) -> None:
        entry = self.entry(record_id)
        status = self._read_status(entry)
        status["writeup"] = {
            "status": status_value,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "error": error,
        }
        self._write_status(entry, status)

    @staticmethod
    def _read_status(entry: Path) -> dict[str, Any]:
        try:
            value = json.loads((entry / "status.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _write_status(entry: Path, status: dict[str, Any]) -> None:
        temporary = entry / ".status.json.tmp"
        temporary.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, entry / "status.json")


class CodexWriteupGenerator:
    def __init__(self, sdk_factory: Callable[[], Any] | None = None) -> None:
        self._sdk_factory = sdk_factory

    async def generate(self, entry: Path, model: str) -> Path:
        factory = self._sdk_factory
        sandbox: Any = None
        if factory is None:
            from openai_codex import AsyncCodex, Sandbox

            factory = AsyncCodex
            sandbox = Sandbox.read_only

        async with factory() as client:
            kwargs: dict[str, Any] = {
                "model": model,
                "cwd": str(entry),
                "developer_instructions": (
                    "You are an independent CTF write-up agent, separate from the solver. "
                    "Use only solver code, event.json, and status.json in the current directory. "
                    "Do not attempt to solve the challenge again. Produce a reproducible Korean "
                    "write-up with overview, analysis, exploitation/solution steps, solver usage, "
                    "and verification. Use the ctf-writeup skill if available. Return Markdown only."
                ),
            }
            if sandbox is not None:
                kwargs["sandbox"] = sandbox
            thread = await client.thread_start(**kwargs)
            result = await thread.run(
                "Read the solved challenge evidence in this directory and generate the complete write-up.md content."
            )
        content = (getattr(result, "final_response", None) or "").strip()
        if not content:
            raise RuntimeError("Write-up 에이전트가 내용을 반환하지 않았습니다.")
        if content.startswith("```markdown") and content.endswith("```"):
            content = content[len("```markdown") : -3].strip()
        output = entry / "write-up.md"
        output.write_text(content + "\n", encoding="utf-8")
        return output
