import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

@dataclass(frozen=True, slots=True)
class Event:
    type: str
    payload: dict[str, Any]
    occurred_at: str

class EventBus:
    def __init__(self) -> None:
        self.queue: asyncio.Queue[Event] = asyncio.Queue()
        self.history: list[Event] = []

    async def publish(self, event_type: str, **payload: Any) -> None:
        event = Event(event_type, payload, datetime.now(timezone.utc).isoformat())
        self.history.append(event)
        await self.queue.put(event)
