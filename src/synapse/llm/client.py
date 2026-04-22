"""Unified async LLM client with retry, timeout, and JSON mode support."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    InternalServerError,
    RateLimitError,
)
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from synapse.llm.json_repair import repair_and_parse_json

logger = logging.getLogger(__name__)


def _is_retryable(exc: BaseException) -> bool:
    """Return True only for transient errors worth retrying.

    Permanent client errors (401 auth, 400 bad request, 403 forbidden, 404 not found)
    are NOT retried — they will never succeed on retry.
    """
    if isinstance(exc, (RateLimitError, InternalServerError)):
        # 429 rate-limit and 5xx server errors are transient
        return True
    if isinstance(exc, APIStatusError):
        # Any other HTTP error (400, 401, 403, 404, 402, …) is permanent
        return False
    if isinstance(exc, APIConnectionError):
        # Network connection issues are transient
        return True
    if isinstance(exc, (APITimeoutError, asyncio.TimeoutError)):
        # Timeout — server is too slow; retrying will just time out again
        return False
    if isinstance(exc, (ConnectionError, OSError)):
        # Low-level network errors are transient
        return True
    # Unknown exceptions — don't retry (fail fast)
    return False


def _log_retry(retry_state: Any) -> None:
    """Log each retry attempt with the error details."""
    exc = retry_state.outcome.exception()
    attempt = retry_state.attempt_number
    if isinstance(exc, APIStatusError):
        logger.warning(
            "LLM call failed (attempt %d/3, status %d): %s",
            attempt,
            exc.status_code,
            exc.message,
        )
    else:
        logger.warning(
            "LLM call failed (attempt %d/3): %s: %s", attempt, type(exc).__name__, exc
        )


_RETRY_POLICY = dict(
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=1, max=10),
    retry=retry_if_exception(_is_retryable),
    before_sleep=_log_retry,
)


class LLMClient:
    """Async OpenAI-compatible LLM client with retry and JSON mode."""

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://openrouter.ai/api/v1",
        model: str = "openrouter/auto",
        timeout: float = 180,
    ) -> None:
        self.model = model
        self.timeout = timeout
        # Disable the SDK's built-in retries — tenacity handles retry logic
        # Set httpx-level timeout so the HTTP connection is actually aborted on timeout
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url, max_retries=0, timeout=timeout)

    async def close(self) -> None:
        """Close the underlying HTTP client to avoid event-loop-closed errors."""
        try:
            await self._client.close()
        except Exception:
            pass

    @retry(**_RETRY_POLICY)
    async def complete(
        self,
        system: str,
        user: str,
        temperature: float = 0.0,
        max_tokens: int = 4096,
        json_mode: bool = False,
    ) -> str:
        """Single-turn completion with retry."""
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        response = await asyncio.wait_for(
            self._client.chat.completions.create(**kwargs),
            timeout=self.timeout,
        )
        content = response.choices[0].message.content or ""
        return content

    @retry(**_RETRY_POLICY)
    async def complete_messages(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> str:
        """Multi-turn completion (used by ReAct reasoning loop)."""
        response = await asyncio.wait_for(
            self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            ),
            timeout=self.timeout,
        )
        content = response.choices[0].message.content or ""
        return content

    async def complete_messages_stream(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ):
        """Multi-turn streaming completion. Yields token strings as they arrive.

        Usage::

            full = ""
            async for token in llm.complete_messages_stream(messages):
                print(token, end="", flush=True)
                full += token
        """
        stream = await self._client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
        )
        async for chunk in stream:
            delta = chunk.choices[0].delta if chunk.choices else None
            if delta and delta.content:
                yield delta.content

    async def complete_json(
        self,
        system: str,
        user: str,
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> dict | list:
        """Completion with JSON mode enabled + auto-repair."""
        raw = await self.complete(
            system=system,
            user=user,
            temperature=temperature,
            max_tokens=max_tokens,
            json_mode=True,
        )
        return repair_and_parse_json(raw)

    async def complete_json_lenient(
        self,
        system: str,
        user: str,
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> dict | list:
        """Try JSON mode first, fall back to free-form + repair.

        Some providers don't support response_format, so we try both.
        Non-retryable errors (auth, bad request, etc.) propagate immediately.
        """
        try:
            return await self.complete_json(
                system=system,
                user=user,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        except APIStatusError:
            # Permanent API errors should propagate, not fall through to retry
            raise
        except Exception:
            logger.debug("JSON mode failed, falling back to free-form + repair")

        raw = await self.complete(
            system=system,
            user=user,
            temperature=temperature,
            max_tokens=max_tokens,
            json_mode=False,
        )
        return repair_and_parse_json(raw)
