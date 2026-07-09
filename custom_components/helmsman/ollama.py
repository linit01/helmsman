"""Minimal async client for a local Ollama server.

Only what Helmsman needs: a single non-streaming chat call with a JSON
schema enforced via Ollama structured outputs. All inference stays on-LAN.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)


class OllamaError(Exception):
    """The Ollama server failed to produce a usable response."""


class OllamaClient:
    """Thin wrapper around Ollama's /api/chat endpoint."""

    def __init__(
        self, session: aiohttp.ClientSession, base_url: str, model: str
    ) -> None:
        """Initialize with a shared HA client session."""
        self._session = session
        self._base_url = base_url.rstrip("/")
        self.model = model

    async def chat_structured(
        self,
        system: str,
        user: str,
        schema: dict[str, Any],
        timeout_s: int,
        temperature: float,
    ) -> dict[str, Any]:
        """One chat round with structured output; returns the parsed JSON."""
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "format": schema,
            "options": {"temperature": temperature},
            "keep_alive": "15m",
        }
        try:
            async with self._session.post(
                f"{self._base_url}/api/chat",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=timeout_s),
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    raise OllamaError(
                        f"Ollama returned HTTP {resp.status}: {body[:200]}"
                    )
                data = await resp.json()
        except (aiohttp.ClientError, TimeoutError) as err:
            raise OllamaError(f"Ollama request failed: {err}") from err

        content = (data.get("message") or {}).get("content") or ""
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as err:
            raise OllamaError(
                f"Ollama returned non-JSON content: {content[:200]}"
            ) from err
        if not isinstance(parsed, dict):
            raise OllamaError(f"Expected a JSON object, got {type(parsed).__name__}")
        return parsed
