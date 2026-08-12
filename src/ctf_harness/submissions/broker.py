from ctf_harness.platforms import PlatformAdapter
class SubmissionBroker:
    def __init__(self, platform: PlatformAdapter):
        self.platform = platform
    async def submit(self, challenge_id: str, candidate: str) -> bool:
        return bool(candidate.strip()) and await self.platform.submit_flag(challenge_id, candidate)
