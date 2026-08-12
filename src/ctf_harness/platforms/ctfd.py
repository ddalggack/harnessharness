from pathlib import Path

from ctf_harness.domain import Challenge


class CTFdPlatformAdapter:
    """Reserved integration boundary for the separately developed CTFd client."""

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    @staticmethod
    def _missing() -> NotImplementedError:
        return NotImplementedError(
            "CTFd adapter is intentionally not implemented in this harness"
        )

    async def list_challenges(self) -> list[Challenge]:
        raise self._missing()

    async def list_solved_challenge_ids(self) -> set[str]:
        raise self._missing()

    async def download_challenge(self, challenge: Challenge, destination: Path) -> Path:
        raise self._missing()

    async def submit_flag(self, challenge_id: str, flag: str) -> bool:
        raise self._missing()