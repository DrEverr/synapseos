"""Unified async LLM client with retry, timeout, and JSON mode support."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from openai import AsyncOpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

from synapse.llm.json_repair import repair_and_parse_json

logger = logging.getLogger(__name__)


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
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
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

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
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
        """
        try:
            return await self.complete_json(
                system=system,
                user=user,
                temperature=temperature,
                max_tokens=max_tokens,
            )
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
