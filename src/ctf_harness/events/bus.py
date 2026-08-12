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
        self._subscribers: set[asyncio.Queue[Event]] = set()

    def subscribe(self) -> asyncio.Queue[Event]:
        queue: asyncio.Queue[Event] = asyncio.Queue()
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[Event]) -> None:
        self._subscribers.discard(queue)

    async def publish(self, event_type: str, **payload: Any) -> None:
        event = Event(event_type, payload, datetime.now(timezone.utc).isoformat())
        self.history.append(event)
        await self.queue.put(event)
        for subscriber in tuple(self._subscribers):
            await subscriber.put(event)
