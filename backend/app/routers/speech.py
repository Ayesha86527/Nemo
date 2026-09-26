"""Speech-to-text — fully on-device via faster-whisper (optional `voice` extra).

Voice is local-only by design: recordings never leave the machine, there is no
per-call cost, and no provider account is needed. The endpoint-based
transcription path was removed along with the voice-mode setting.
"""

from fastapi import APIRouter, File, HTTPException, UploadFile

from app.speech import local_asr

router = APIRouter(prefix="/api/speech", tags=["speech"])


@router.post("/transcribe")
async def transcribe(file: UploadFile = File(...)):
    audio = await file.read()
    if not audio:
        raise HTTPException(400, detail={"error": "Empty audio recording."})

    if not local_asr.is_available():
        raise HTTPException(
            400,
            detail={
                "error": "Voice input runs on-device and needs the optional faster-whisper extra.",
                "hint": local_asr.INSTALL_HINT,
            },
        )
    try:
        text = await local_asr.transcribe(audio)
    except local_asr.LocalASRNotInstalled as exc:
        raise HTTPException(400, detail={"error": "Local voice is not installed.", "hint": str(exc)})
    except local_asr.LocalASRError as exc:
        raise HTTPException(500, detail={"error": str(exc), "hint": "Check the recording, or try again."})

    return {"text": text}
