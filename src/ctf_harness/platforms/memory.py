import json
from pathlib import Path
from ctf_harness.domain import Challenge

class MemoryPlatformAdapter:
    def __init__(self, challenges: list[Challenge]):
        self._challenges = {challenge.id: challenge for challenge in challenges}
        self._solved: set[str] = set()
        self.fail_next_list = False
        self.submissions: list[tuple[str, str]] = []

    async def list_challenges(self) -> list[Challenge]:
        if self.fail_next_list:
            self.fail_next_list = False
            raise RuntimeError("simulated platform failure")
        return list(self._challenges.values())

    async def list_solved_challenge_ids(self) -> set[str]:
        return set(self._solved)

    def add_challenge(self, challenge: Challenge) -> None:
        self._challenges[challenge.id] = challenge

    def mark_solved(self, challenge_id: str) -> None:
        self._solved.add(challenge_id)

    async def download_challenge(self, challenge: Challenge, destination: Path) -> Path:
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "challenge.json").write_text(json.dumps({"id": challenge.id, "title": challenge.title, "category": challenge.category}, ensure_ascii=False, indent=2), encoding="utf-8")
        return destination

    async def submit_flag(self, challenge_id: str, flag: str) -> bool:
        self.submissions.append((challenge_id, flag))
        return bool(flag)
