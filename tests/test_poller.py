import asyncio
import unittest

from ctf_harness.domain import Challenge
from ctf_harness.platforms import MemoryPlatformAdapter
from ctf_harness.poller import PlatformPoller, PollEventKind


class PollerTests(unittest.TestCase):
    def test_seed_is_silent_and_changes_emit_events(self):
        async def scenario() -> None:
            first = Challenge("one", "One", "pwn")
            second = Challenge("two", "Two", "rev")
            platform = MemoryPlatformAdapter([first])
            poller = PlatformPoller(platform, interval_s=0.01)

            await poller.seed()
            self.assertEqual(poller.known_challenge_ids, {"one"})
            self.assertEqual(poller.drain_events(), [])

            platform.add_challenge(second)
            platform.mark_solved("one")
            await poller.poll_once()

            events = poller.drain_events()
            self.assertEqual(
                [(event.kind, event.challenge_id) for event in events],
                [
                    (PollEventKind.NEW_CHALLENGE, "two"),
                    (PollEventKind.CHALLENGE_SOLVED, "one"),
                ],
            )
            self.assertEqual(events[0].challenge, second)

        asyncio.run(scenario())

    def test_failed_poll_keeps_last_known_state(self):
        async def scenario() -> None:
            platform = MemoryPlatformAdapter([Challenge("one", "One", "pwn")])
            poller = PlatformPoller(platform)
            await poller.seed()
            platform.fail_next_list = True

            await poller.poll_once()

            self.assertEqual(poller.known_challenge_ids, {"one"})
            self.assertEqual(poller.drain_events(), [])

        asyncio.run(scenario())
