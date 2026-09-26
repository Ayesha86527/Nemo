"""On-device speech-to-text via faster-whisper (optional dependency).

Installed with `pip install -e ".[voice]"`. Models are downloaded once from
Hugging Face into ~/.nemo/models/whisper and everything after that is fully
offline — recordings never leave the machine.
"""

import asyncio
import io
from pathlib import Path

from app.db.engine import DATA_DIR

DEFAULT_MODEL = "base"  # ~150 MB, multilingual, fast on CPU
MODELS_DIR = Path(DATA_DIR) / "models" / "whisper"

INSTALL_HINT = (
    "Local voice input needs the optional faster-whisper extra.\n"
    'Install it with: pip install -e ".[voice]" (or switch Voice input to '
    '"Endpoint" in Settings if your provider offers transcription).'
)


class LocalASRNotInstalled(Exception):
    """faster-whisper is not installed."""


class LocalASRError(Exception):
    """Transcription failed on-device."""


_model = None
_model_name = ""
_load_lock = asyncio.Lock()


def is_available() -> bool:
    try:
        import faster_whisper  # noqa: F401
        return True
    except ImportError:
        return False


def _get_model():
    """Lazily load (and cache) the whisper model; downloads on first use."""
    global _model, _model_name
    if _model is not None and _model_name == DEFAULT_MODEL:
        return _model
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise LocalASRNotInstalled(INSTALL_HINT) from exc
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    try:
        _model = WhisperModel(
            DEFAULT_MODEL,
            device="cpu",
            compute_type="int8",
            download_root=str(MODELS_DIR),
        )
        _model_name = DEFAULT_MODEL
        return _model
    except Exception as exc:
        raise LocalASRError(f"Could not load the local whisper model: {exc}") from exc


async def transcribe(audio: bytes) -> str:
    """Transcribe a recording (any container PyAV decodes: webm/opus, wav…) off-thread."""
    def _run() -> str:
        model = _get_model()
        try:
            segments, _info = model.transcribe(io.BytesIO(audio))
            return " ".join(s.text.strip() for s in segments).strip()
        except Exception as exc:
            raise LocalASRError(f"Local transcription failed: {exc}") from exc

    return await asyncio.to_thread(_run)
