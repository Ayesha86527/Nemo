"""Speech-to-text via the configured OpenAI-compatible endpoint.

Electron's Chromium ships no usable web speech service, so the renderer
records audio locally (MediaRecorder) and this router transcribes it at
the provider's /audio/transcriptions (whisper) using the saved API key.
"""

import asyncio
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, File, HTTPException, UploadFile

from app.llm.providers import (
    MAX_ATTEMPTS,
    RETRYABLE_STATUS,
    get_shared_client,
    normalize_base_url,
    retry_delay,
)
from app.llm.router import get_settings

router = APIRouter(prefix="/api/speech", tags=["speech"])

TRANSCRIBE_TIMEOUT = 90.0


def _candidate_models(base_url: str) -> list[str]:
    host = (urlparse(base_url).hostname or "").lower()
    if "groq" in host:
        return ["whisper-large-v3", "whisper-1"]
    if "mistral" in host:
        return ["voxtral-16b-2504", "whisper-1"]
    return ["whisper-1", "whisper-large-v3"]


def _http_error(status_code: int, body: str) -> HTTPException:
    if status_code in (401, 403):
        return HTTPException(
            status_code,
            detail={
                "error": "The LLM endpoint rejected the API key during transcription.",
                "hint": "Check the API key in Settings.",
            },
        )
    if status_code == 404:
        return HTTPException(
            status_code,
            detail={
                "error": "The LLM endpoint has no /audio/transcriptions route.",
                "hint": "Use an endpoint with speech-to-text (e.g. Groq or OpenAI), or type instead.",
            },
        )
    if status_code == 429:
        return HTTPException(
            status_code,
            detail={
                "error": "Transcription rate limit exceeded.",
                "hint": "Nemo already retried automatically — wait a minute and try again.",
            },
        )
    return HTTPException(
        status_code if 400 <= status_code < 600 else 502,
        detail={"error": f"Transcription failed (HTTP {status_code}): {body[:200]}"},
    )


@router.post("/transcribe")
async def transcribe(file: UploadFile = File(...)):
    settings = await get_settings()
    base_url = normalize_base_url(settings.custom_base_url)
    if not base_url:
        raise HTTPException(
            400,
            detail={
                "error": "No LLM endpoint configured.",
                "hint": "Set the endpoint in Settings, e.g. https://api.groq.com/openai/v1",
            },
        )
    if not settings.custom_api_key:
        raise HTTPException(
            400,
            detail={
                "error": "The LLM endpoint needs an API key for transcription.",
                "hint": "Add your API key in Settings.",
            },
        )

    audio = await file.read()
    if not audio:
        raise HTTPException(400, detail={"error": "Empty audio recording."})
    filename = file.filename or "recording.webm"
    content_type = file.content_type or "audio/webm"

    last_exc: HTTPException | None = None
    for model in _candidate_models(base_url):
        resp: httpx.Response | None = None
        for attempt in range(MAX_ATTEMPTS):
            try:
                resp = await get_shared_client().post(
                    f"{base_url}/audio/transcriptions",
                    headers={"Authorization": f"Bearer {settings.custom_api_key}"},
                    files={"file": (filename, audio, content_type)},
                    data={"model": model},
                    timeout=TRANSCRIBE_TIMEOUT,
                )
            except httpx.TimeoutException:
                if attempt < MAX_ATTEMPTS - 1:
                    await asyncio.sleep(retry_delay(attempt, None))
                    continue
                raise HTTPException(
                    504,
                    detail={"error": "Transcription timed out.", "hint": "Try a shorter recording."},
                )
            except httpx.ConnectError:
                if attempt < MAX_ATTEMPTS - 1:
                    await asyncio.sleep(retry_delay(attempt, None))
                    continue
                raise HTTPException(
                    502,
                    detail={
                        "error": "Could not connect to the LLM endpoint.",
                        "hint": "Check the base URL in Settings and that the service is running.",
                    },
                )
            except httpx.HTTPError as exc:
                raise HTTPException(502, detail={"error": f"Transcription request failed: {exc}"})

            if resp.status_code < 400:
                return {"text": (resp.json().get("text") or "").strip()}
            if resp.status_code in RETRYABLE_STATUS and attempt < MAX_ATTEMPTS - 1:
                await asyncio.sleep(retry_delay(attempt, resp))
                continue
            break

        assert resp is not None
        # Wrong model name for this provider — try the next candidate once.
        if resp.status_code in (400, 404) and "model" in resp.text.lower():
            last_exc = _http_error(resp.status_code, resp.text)
            continue
        raise _http_error(resp.status_code, resp.text)

    raise last_exc or HTTPException(502, detail={"error": "Transcription failed."})
