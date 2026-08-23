"""Reviewer-facing limits for expensive AI-backed conversation turns."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from webchat.persistence.repositories import TurnRepository


class TurnLimitReachedError(RuntimeError):
    """Raised when a new model-backed turn cannot be admitted safely."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class TurnAdmission:
    """Apply global daily and concurrency limits while preserving idempotent retries."""

    def __init__(
        self,
        turns: TurnRepository,
        *,
        daily_limit: int,
        max_concurrent: int,
    ):
        self._turns = turns
        self._daily_limit = daily_limit
        self._capacity = asyncio.Semaphore(max_concurrent)

    @asynccontextmanager
    async def admit(self, conversation_id: str, client_message_id: str):
        existing = self._turns.get_by_client_id(conversation_id, client_message_id)
        if existing is not None:
            yield
            return

        if self._daily_limit and self._turns.count_started_since(_utc_day_start()) >= self._daily_limit:
            raise TurnLimitReachedError(
                "DAILY_REVIEW_LIMIT_REACHED",
                "This review environment has reached its daily AI usage limit. Please try tomorrow.",
            )

        try:
            await asyncio.wait_for(self._capacity.acquire(), timeout=0.1)
        except TimeoutError as error:
            raise TurnLimitReachedError(
                "AI_CAPACITY_REACHED",
                "The review environment is handling other AI requests. Please retry shortly.",
            ) from error
        try:
            yield
        finally:
            self._capacity.release()


def _utc_day_start() -> str:
    today = datetime.now(UTC).date().isoformat()
    return f"{today}T00:00:00Z"
