"""Minimal async client for a local Ollama server.

Only what Helmsman needs: a single non-streaming chat call with a JSON
schema enforced via Ollama structured outputs, plus a speed probe used
to auto-tune review timeouts. All inference stays on-LAN.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

# ~2700 chars (~650 tokens) so the probe measures prompt throughput on a
# realistic batch, not a handful of tokens.
_PROBE_FILLER = "The quick brown fox jumps over the lazy dog. " * 60


class OllamaError(Exception):
    """The Ollama server failed to produce a usable response."""


def _parse_param_size(value: Any) -> float | None:
    """Ollama's '7.6B' / '778M' parameter_size string as billions."""
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"([\d.]+)\s*([MB])", value.strip().upper())
    if not match:
        return None
    number = float(match.group(1))
    return number / 1000 if match.group(2) == "M" else number


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

    async def unload(self, timeout_s: int = 30) -> None:
        """Free this model's memory on the server now (keep_alive 0)."""
        payload = {
            "model": self.model,
            "messages": [],
            "stream": False,
            "keep_alive": 0,
        }
        await self._post_chat(payload, timeout_s)

    async def list_models(self, timeout_s: int = 15) -> list[dict[str, Any]]:
        """Models available on the server (/api/tags), with metadata.

        Each entry: {"name", "families" (lowercased), "param_b" (float
        parameter count in billions, or None when unreported)}.
        """
        try:
            async with self._session.get(
                f"{self._base_url}/api/tags",
                timeout=aiohttp.ClientTimeout(total=timeout_s),
            ) as resp:
                if resp.status != 200:
                    raise OllamaError(
                        f"Ollama returned HTTP {resp.status} listing models"
                    )
                data = await resp.json()
        except TimeoutError as err:
            raise OllamaError("Ollama timed out listing models") from err
        except aiohttp.ClientError as err:
            raise OllamaError(
                f"Ollama request failed: {type(err).__name__}: {err}"
            ) from err
        models = []
        for m in data.get("models", []):
            if not isinstance(m, dict) or not isinstance(m.get("name"), str):
                continue
            details = m.get("details") or {}
            models.append(
                {
                    "name": m["name"],
                    "families": [
                        str(f).lower()
                        for f in (details.get("families") or [])
                    ],
                    "param_b": _parse_param_size(
                        details.get("parameter_size")
                    ),
                }
            )
        return models

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
        return await self.chat_structured_messages(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            schema,
            timeout_s,
            temperature,
        )

    async def chat_structured_messages(
        self,
        messages: list[dict[str, str]],
        schema: dict[str, Any],
        timeout_s: int,
        temperature: float,
    ) -> dict[str, Any]:
        """Structured chat over a full message history (retry loops)."""
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "format": schema,
            # 16k context: large automation configs plus self-correction
            # history (prior attempt + rejection feedback) must fit.
            "options": {"temperature": temperature, "num_ctx": 16384},
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
