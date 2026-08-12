import asyncio
import unittest

from ctf_harness.submissions import SubmissionBroker, SubmissionStatus


class FakePlatform:
    def __init__(self, results):
        self.results = list(results)
        self.submissions = []

    async def submit_flag(self, challenge_id, candidate):
        self.submissions.append((challenge_id, candidate))
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class SubmissionBrokerTests(unittest.TestCase):
    def test_deduplicates_candidates_and_enforces_wrong_limit(self):
        async def scenario():
            platform = FakePlatform([False, False])
            broker = SubmissionBroker(platform, max_wrong_submissions=2)

            first = await broker.submit("web-1", "FLAG{one}")
            duplicate = await broker.submit("web-1", "FLAG{one}")
            second = await broker.submit("web-1", "FLAG{two}")
            limited = await broker.submit("web-1", "FLAG{three}")

            self.assertEqual(first.status, SubmissionStatus.REJECTED)
            self.assertEqual(duplicate.status, SubmissionStatus.DUPLICATE)
            self.assertEqual(second.status, SubmissionStatus.WRONG_LIMIT)
            self.assertEqual(limited.status, SubmissionStatus.WRONG_LIMIT)
            self.assertEqual(len(platform.submissions), 2)

        asyncio.run(scenario())

    def test_serializes_submissions_for_each_challenge(self):
        async def scenario():
            entered = asyncio.Event()
            release = asyncio.Event()
            active = 0
            peak = 0

            class Platform:
                async def submit_flag(self, challenge_id, candidate):
                    nonlocal active, peak
                    active += 1
                    peak = max(peak, active)
                    entered.set()
                    await release.wait()
                    active -= 1
                    return False

            broker = SubmissionBroker(Platform(), max_wrong_submissions=3)
            first = asyncio.create_task(broker.submit("pwn-1", "FLAG{one}"))
            await entered.wait()
            second = asyncio.create_task(broker.submit("pwn-1", "FLAG{two}"))
            await asyncio.sleep(0)
            release.set()
            await asyncio.gather(first, second)
            self.assertEqual(peak, 1)

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
