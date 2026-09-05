"""Gemini Model Provider adapter using HTTP API with structured JSON schema enforcement."""

import json
import logging
from typing import Any, Dict, Optional

import httpx

from app.ai.providers.base import ModelProvider, ModelTimeoutError, ModelUnavailableError
from app.config import settings

logger = logging.getLogger("iro.ai.gemini_provider")


class GeminiModelProvider(ModelProvider):
    """Production provider adapter communicating with Google Gemini API."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model_name: Optional[str] = None,
        timeout_sec: float = 3.0,
    ):
        self.api_key = api_key or settings.GEMINI_API_KEY
        self.model_name = model_name or settings.GEMINI_MODEL_FAST
        self.timeout_sec = timeout_sec

    async def generate_recommendation(
        self,
        prompt: str,
        system_prompt: str,
        context: Dict[str, Any],
    ) -> str:
        if not self.api_key:
            raise ModelUnavailableError("GEMINI_API_KEY is not configured.")

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model_name}:generateContent?key={self.api_key}"

        payload = {
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": prompt}],
                }
            ],
            "systemInstruction": {
                "parts": [{"text": system_prompt}],
            },
            "generationConfig": {
                "responseMimeType": "application/json",
                "temperature": 0.1,
            },
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout_sec) as client:
                resp = await client.post(url, json=payload)

            if resp.status_code != 200:
                logger.error(f"[GEMINI ERROR] HTTP {resp.status_code}: {resp.text}")
                raise ModelUnavailableError(f"Gemini API returned status {resp.status_code}")

            data = resp.json()
            candidates = data.get("candidates", [])
            if not candidates:
                raise ModelUnavailableError("Gemini API returned empty candidate response.")

            parts = candidates[0].get("content", {}).get("parts", [])
            if not parts:
                raise ModelUnavailableError("Gemini API returned content without parts.")

            return parts[0].get("text", "")

        except httpx.TimeoutException as err:
            logger.warning(f"[GEMINI TIMEOUT] Request exceeded {self.timeout_sec}s: {err}")
            raise ModelTimeoutError(f"Gemini API timed out after {self.timeout_sec}s")
        except httpx.RequestError as err:
            logger.error(f"[GEMINI NETWORK ERROR] {err}")
            raise ModelUnavailableError(f"Network error communicating with Gemini API: {err}")
