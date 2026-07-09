"""Minimal async client for a local Ollama server.

Only what Helmsman needs: a single non-streaming chat call with a JSON
schema enforced via Ollama structured outputs, plus a speed probe used
to auto-tune review timeouts. All inference stays on-LAN.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

# ~2700 chars (~650 tokens) so the probe measures prompt throughput on a
# realistic batch, not a handful of tokens.
_PROBE_FILLER = "The quick brown fox jumps over the lazy dog. " * 60


class OllamaError(Exception):
    """The Ollama server failed to produce a usable response."""


def _extract_stats(data: dict[str, Any]) -> dict[str, float] | None:
    """Tokens/sec rates from Ollama's response metadata, if present."""
    stats: dict[str, float] = {}
    try:
        if data.get("prompt_eval_count") and data.get("prompt_eval_duration"):
            stats["prompt_tps"] = data["prompt_eval_count"] / (
                data["prompt_eval_duration"] / 1e9
            )
        if data.get("eval_count") and data.get("eval_duration"):
            stats["gen_tps"] = data["eval_count"] / (
                data["eval_duration"] / 1e9
            )
    except (TypeError, ZeroDivisionError):
        return None
    return stats or None


class OllamaClient:
    """Thin wrapper around Ollama's /api/chat endpoint."""

    def __init__(
        self, session: aiohttp.ClientSession, base_url: str, model: str
    ) -> None:
        """Initialize with a shared HA client session."""
        self._session = session
        self._base_url = base_url.rstrip("/")
        self.model = model
        self.last_stats: dict[str, float] | None = None

    async def _post_chat(
        self, payload: dict[str, Any], timeout_s: int
    ) -> dict[str, Any]:
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
        except TimeoutError as err:
            raise OllamaError(
                f"Ollama request timed out after {timeout_s}s"
            ) from err
        except aiohttp.ClientError as err:
            raise OllamaError(
                f"Ollama request failed: {type(err).__name__}: {err}"
            ) from err
        self.last_stats = _extract_stats(data)
        return data

    async def probe_speed(
        self, timeout_s: int = 180
    ) -> dict[str, float] | None:
        """Measure model speed with a short request (also warms the model).

        Returns {"prompt_tps", "gen_tps"} or None when the server omits
        the timing metadata.
        """
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": _PROBE_FILLER
                    + "\nReply with the single word: ok",
                }
            ],
            "stream": False,
            "options": {"temperature": 0, "num_predict": 16},
            "keep_alive": "15m",
        }
        data = await self._post_chat(payload, timeout_s)
        return _extract_stats(data)

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
            # Ollama defaults to a 2048-token context, which large
            # automation configs plus the entity inventory can overflow.
            "options": {"temperature": temperature, "num_ctx": 8192},
            "keep_alive": "15m",
        }
        data = await self._post_chat(payload, timeout_s)

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
