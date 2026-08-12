from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

from ctf_harness.domain import Challenge


class CTFdAPIError(RuntimeError):
    """Raised when CTFd returns an HTTP or API-level failure."""


class CTFdConnectionError(CTFdAPIError, ConnectionError):
    """Retryable CTFd transport or temporary server failure."""


class CTFdPlatformAdapter:
    """CTFd v1 API adapter using token authentication and challenge file URLs."""

    def __init__(self, base_url: str, token: str | None = None, timeout_s: float = 30.0):
        if not base_url.startswith(("http://", "https://")):
            raise ValueError("base_url must start with http:// or https://")
        if timeout_s <= 0:
            raise ValueError("timeout_s must be positive")
        self.base_url = base_url.rstrip("/") + "/"
        self.token = token
        self.timeout_s = timeout_s
        self._details: dict[str, dict[str, Any]] = {}
        self._solved_ids: set[str] = set()

    def _headers(self, url: str) -> dict[str, str]:
        headers = {"Accept": "application/json", "User-Agent": "ddalggack/0.1"}
        base = urlparse(self.base_url)
        target = urlparse(url)
        if self.token and (target.scheme, target.netloc) == (base.scheme, base.netloc):
            headers["Authorization"] = f"Token {self.token}"
        return headers

    def _request_sync(
        self,
        method: str,
        path_or_url: str,
        payload: dict[str, Any] | None = None,
        *,
        expect_json: bool = True,
    ) -> Any:
        url = (
            path_or_url
            if path_or_url.startswith(("http://", "https://"))
            else urljoin(self.base_url, path_or_url.lstrip("/"))
        )
        body = json.dumps(payload).encode() if payload is not None else None
        headers = self._headers(url)
        if body is not None:
            headers["Content-Type"] = "application/json"
        request = Request(url, data=body, headers=headers, method=method)
        try:
            with urlopen(request, timeout=self.timeout_s) as response:
                raw = response.read()
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            error_type = CTFdConnectionError if exc.code == 429 or exc.code >= 500 else CTFdAPIError
            raise error_type(f"CTFd {method} {url} failed with HTTP {exc.code}: {detail}") from exc
        except URLError as exc:
            raise CTFdConnectionError(f"CTFd {method} {url} failed: {exc.reason}") from exc
        if not expect_json:
            return raw
        try:
            result = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise CTFdAPIError(f"CTFd {method} {url} returned invalid JSON") from exc
        if not isinstance(result, dict) or result.get("success") is not True:
            raise CTFdAPIError(f"CTFd {method} {url} returned an unsuccessful response")
        return result.get("data")

    async def _request(
        self,
        method: str,
        path_or_url: str,
        payload: dict[str, Any] | None = None,
        *,
        expect_json: bool = True,
    ) -> Any:
        return await asyncio.to_thread(
            self._request_sync, method, path_or_url, payload, expect_json=expect_json
        )

    @staticmethod
    def _connection(connection_info: Any) -> tuple[str | None, int | None]:
        if not isinstance(connection_info, str):
            return None, None
        parsed = urlparse(connection_info.strip())
        if parsed.scheme in {"http", "https"} and parsed.hostname:
            return parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80)
        match = re.search(r"(?:^|\s)(?:nc\s+)?([^\s:]+)[:\s](\d{1,5})(?:\s|$)", connection_info.strip())
        if match is None:
            return None, None
        port = int(match.group(2))
        return (match.group(1), port) if port <= 65535 else (None, None)

    async def list_challenges(self) -> list[Challenge]:
        summaries = await self._request("GET", "/api/v1/challenges")
        if not isinstance(summaries, list):
            raise CTFdAPIError("CTFd challenge list data must be an array")
        challenges: list[Challenge] = []
        solved: set[str] = set()
        details: dict[str, dict[str, Any]] = {}
        for summary in summaries:
            challenge_id = str(summary["id"])
            detail = await self._request("GET", f"/api/v1/challenges/{challenge_id}")
            if not isinstance(detail, dict):
                raise CTFdAPIError(f"CTFd challenge {challenge_id} data must be an object")
            details[challenge_id] = detail
            if bool(summary.get("solved_by_me", detail.get("solved_by_me", False))):
                solved.add(challenge_id)
            host, port = self._connection(detail.get("connection_info"))
            description = str(detail.get("description") or "")
            connection_info = detail.get("connection_info")
            if (
                isinstance(connection_info, str)
                and urlparse(connection_info.strip()).scheme in {"http", "https"}
            ):
                description = f"{description}\n\nConnection: {connection_info.strip()}".strip()
            challenges.append(
                Challenge(
                    id=challenge_id,
                    title=str(detail.get("name", summary.get("name", challenge_id))),
                    category=str(detail.get("category", summary.get("category", "general"))),
                    description=description,
                    host=host,
                    port=port,
                )
            )
        self._details = details
        self._solved_ids = solved
        return challenges

    async def list_solved_challenge_ids(self) -> set[str]:
        await self.list_challenges()
        return set(self._solved_ids)

    async def download_challenge(self, challenge: Challenge, destination: Path) -> Path:
        detail = self._details.get(challenge.id)
        if detail is None:
            detail = await self._request("GET", f"/api/v1/challenges/{challenge.id}")
            if not isinstance(detail, dict):
                raise CTFdAPIError(f"CTFd challenge {challenge.id} data must be an object")
            self._details[challenge.id] = detail
        destination.mkdir(parents=True, exist_ok=True)
        metadata = {
            "id": challenge.id,
            "title": challenge.title,
            "category": challenge.category,
            "description": challenge.description,
            "host": challenge.host,
            "port": challenge.port,
        }
        (destination / "challenge.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        for file_url in detail.get("files") or []:
            parsed = urlparse(str(file_url))
            name = PurePosixPath(parsed.path).name
            if not name:
                raise CTFdAPIError(f"CTFd challenge {challenge.id} returned an invalid file URL")
            content = await self._request("GET", str(file_url), expect_json=False)
            (destination / name).write_bytes(content)
        return destination

    async def submit_flag(self, challenge_id: str, flag: str) -> bool:
        try:
            numeric_id: int | str = int(challenge_id)
        except ValueError:
            numeric_id = challenge_id
        data = await self._request(
            "POST",
            "/api/v1/challenges/attempt",
            {"challenge_id": numeric_id, "submission": flag},
        )
        return isinstance(data, dict) and data.get("status") == "correct"
