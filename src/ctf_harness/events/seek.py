from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import TextIO


DIM = "\033[2m"
BOLD = "\033[1m"
CYAN = "\033[36m"
MAGENTA = "\033[35m"
BLUE = "\033[34m"
YELLOW = "\033[33m"
GREEN = "\033[32m"
RED = "\033[31m"
RESET = "\033[0m"


def find_run_dir(runs_root: Path, run_id: str | None) -> Path:
    root = runs_root.resolve()
    if run_id is not None:
        run_dir = root / run_id
        if not (run_dir / "events.jsonl").is_file():
            raise FileNotFoundError(f"run event log not found: {run_dir / 'events.jsonl'}")
        return run_dir
    candidates = (
        [path for path in root.iterdir() if path.is_dir() and (path / "events.jsonl").is_file()]
        if root.is_dir()
        else []
    )
    if not candidates:
        raise FileNotFoundError(f"no run event logs under {root}")
    return max(candidates, key=lambda path: (path / "events.jsonl").stat().st_mtime)


def _style(text: str, *codes: str, color: bool) -> str:
    return f"{''.join(codes)}{text}{RESET}" if color else text


def _clean(text: object) -> str:
    value = str(text or "").strip()
    return value.replace("**", "").replace("__", "").replace("`", "")


def _compact(text: object, limit: int = 260) -> str:
    value = " ".join(_clean(text).split())
    return value if len(value) <= limit else value[: limit - 1] + "…"


def _pretty(value: object) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, indent=2)


def _message_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        if parts:
            return "\n".join(parts)
    return _pretty(content)


def _assignment_description(text: str) -> str | None:
    match = re.search(r"Description:\s*(.*?)\nConnection:", text, re.DOTALL)
    return _compact(match.group(1), 360) if match else None


def _assistant_summary(text: str) -> tuple[str, str] | None:
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    kind = str(payload.get("kind", "message")).replace("_", " ")
    summary = _compact(payload.get("summary", ""), 500)
    candidate = payload.get("flag_candidate")
    if candidate:
        summary = f"{summary}\n  candidate: {candidate}"
    return kind, summary


def _duration(payload: dict[str, object]) -> str:
    value = payload.get("duration_ms")
    if not isinstance(value, (int, float)):
        return ""
    return f" · {value / 1000:.1f}s" if value >= 1000 else f" · {value}ms"


def _output_preview(output: object, command: object) -> list[str]:
    text = str(output or "").strip()
    if not text:
        return []
    lines = text.splitlines()
    if "SKILL.md" in str(command):
        return [f"loaded skill instructions · {len(lines)} lines (use --raw to inspect)"]
    shown = lines[:12]
    clipped = [line if len(line) <= 180 else line[:179] + "…" for line in shown]
    hidden = len(lines) - len(shown)
    if hidden > 0:
        clipped.append(f"… {hidden} more lines hidden (use --raw for the full event)")
    return clipped


def format_worker_event(event: dict[str, object], *, color: bool = False) -> str | None:
    event_type = str(event.get("type", ""))
    payload = event.get("payload")
    if not isinstance(payload, dict):
        return None
    status = payload.get("status")
    if event_type in {"worker.message", "worker.reasoning"} and status != "completed":
        return None
    clock = _style(str(event.get("occurred_at", ""))[11:19], DIM, color=color)
    prefix = f"{clock} "

    if event_type == "worker.started":
        title = str(payload.get("challenge_title") or payload.get("challenge_id", "unknown"))
        category = payload.get("challenge_category")
        suffix = f" · {category}" if category else ""
        return "\n" + _style(f"━━ {title}{suffix} ━━", BOLD, CYAN, color=color)
    if event_type == "worker.turn_started":
        return prefix + _style(f"╭─ Turn {payload.get('turn')}", BOLD, BLUE, color=color)
    if event_type == "worker.turn_completed":
        return prefix + _style(
            f"╰─ Turn {payload.get('turn')} · {payload.get('elapsed_s')}s", DIM, color=color
        )
    if event_type == "worker.heartbeat":
        return prefix + _style(f"… still working · {payload.get('elapsed_s')}s", DIM, color=color)
    if event_type == "worker.message":
        role = payload.get("role")
        content = _message_text(payload.get("content"))
        if role == "user" and "You received one CTF challenge" in content:
            title = payload.get("challenge_title") or payload.get("challenge_id")
            description = _assignment_description(content)
            lines = [prefix + _style(f"› Assignment · {title}", BOLD, BLUE, color=color)]
            if description:
                lines.append("  " + description)
            return "\n".join(lines)
        if role == "assistant":
            parsed = _assistant_summary(content)
            if parsed is not None:
                kind, summary = parsed
                return prefix + _style(f"● {kind}", BLUE, color=color) + f"\n  {summary}"
        return prefix + _style(f"› {role}", BLUE, color=color) + f"\n  {_compact(content, 600)}"
    if event_type == "worker.reasoning":
        summary = _clean(payload.get("summary"))
        return prefix + _style(f"◆ {summary}", MAGENTA, color=color)
    if event_type == "worker.tool":
        tool = str(payload.get("tool", "tool"))
        if status == "started":
            subject = payload.get("command") or payload.get("query") or payload.get("arguments")
            lines = [prefix + _style(f"• {tool}", YELLOW, color=color)]
            if subject not in (None, "", [], {}):
                lines.append("  " + _compact(subject, 300))
            if payload.get("intent"):
                lines.append(_style(f"  ↳ {_compact(payload['intent'], 300)}", DIM, color=color))
            return "\n".join(lines)
        exit_code = payload.get("exit_code")
        failed = exit_code not in (None, 0) or payload.get("error") not in (None, "", {})
        icon = "✗" if failed else "✓"
        tone = RED if failed else GREEN
        result = f"{icon} {tool}"
        if exit_code is not None:
            result += f" · exit {exit_code}"
        result += _duration(payload)
        lines = [prefix + _style(result, tone, color=color)]
        for line in _output_preview(payload.get("output") or payload.get("result") or payload.get("error"), payload.get("command")):
            lines.append(_style(f"  │ {line}", DIM, color=color))
        return "\n".join(lines)
    if event_type == "worker.file_change":
        if status == "started":
            return prefix + _style("• editing files", YELLOW, color=color)
        changes = payload.get("changes", [])
        return prefix + _style("✓ files changed", GREEN, color=color) + f"\n  {_compact(changes, 600)}"
    if event_type == "submission.started":
        return prefix + _style("↗ submitting flag", YELLOW, color=color)
    if event_type == "submission.accepted":
        return prefix + _style("✓ flag accepted", BOLD, GREEN, color=color)
    if event_type in {"submission.rejected", "submission.wrong_limit", "submission.error"}:
        return prefix + _style(f"✗ {event_type.removeprefix('submission.').replace('_', ' ')}", RED, color=color)
    return None


def seek_worker(
    runs_root: Path,
    worker_number: int,
    *,
    run_id: str | None = None,
    follow: bool = True,
    raw: bool = False,
    stream: TextIO,
) -> int:
    run_dir = find_run_dir(runs_root, run_id)
    event_path = run_dir / "events.jsonl"
    color = bool(getattr(stream, "isatty", lambda: False)()) and os.environ.get("NO_COLOR") is None
    heading = f"worker {worker_number} · run {run_dir.name}"
    print(_style(heading, BOLD, CYAN, color=color), file=stream, flush=True)
    with event_path.open(encoding="utf-8") as handle:
        initial_lines = handle.readlines()
        initial_events: list[dict[str, object]] = []
        active_worker_id: str | None = None
        for line in initial_lines:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            payload = event.get("payload", {})
            if not isinstance(payload, dict) or payload.get("worker_number") != worker_number:
                continue
            initial_events.append(event)
            if event.get("type") == "worker.started" and isinstance(payload.get("worker_id"), str):
                active_worker_id = payload["worker_id"]

        for event in initial_events:
            payload = event.get("payload", {})
            if active_worker_id is not None and payload.get("worker_id") != active_worker_id:
                continue
            rendered = (
                json.dumps(event, ensure_ascii=False)
                if raw
                else format_worker_event(event, color=color)
            )
            if rendered:
                print(rendered, file=stream, flush=True)

        while True:
            line = handle.readline()
            if not line:
                if not follow:
                    return 0
                time.sleep(0.2)
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            payload = event.get("payload", {})
            if not isinstance(payload, dict) or payload.get("worker_number") != worker_number:
                continue
            if event.get("type") == "worker.started" and isinstance(payload.get("worker_id"), str):
                active_worker_id = payload["worker_id"]
            if active_worker_id is not None and payload.get("worker_id") != active_worker_id:
                continue
            rendered = (
                json.dumps(event, ensure_ascii=False)
                if raw
                else format_worker_event(event, color=color)
            )
            if rendered:
                print(rendered, file=stream, flush=True)
