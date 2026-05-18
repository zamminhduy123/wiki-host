"""
tts.py — FastAPI router for TTS endpoints.

Provides text-to-speech endpoints using VieNeu-TTS GPU backend.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
import tempfile
import uuid
from services.tts_service import tts_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/tts", tags=["Text-to-Speech"])


class TTSRequest(BaseModel):
    """Request payload for TTS synthesis."""
    text: str = Field(..., description="Text to convert to speech")
    self_clone: bool = Field(..., description="Whether to use self clone voice of the TTS service")
    voice_id: Optional[str] = Field(
        default=None,
        description="Optional preset voice ID. Use GET /tts/voices to list available voices."
    )
    emotion: Optional[str] = Field(
        default=None,
        description="Emotion mode: 'natural' (conversational) or 'storytelling' (narrative)"
    )
    output_dir: Optional[str] = Field(
        default="outputs/tts",
        description="Directory to save output files"
    )


class TTSResponse(BaseModel):
    """Response for TTS synthesis."""
    success: bool
    message: str
    audio_file: Optional[str] = None
    voice_id: Optional[str] = None


class VoiceDescription(BaseModel):
    """Description of a available voice."""
    description: str
    voice_id: str


@router.get("/voices", response_model=list[VoiceDescription], summary="List available voices")
async def list_voices() -> JSONResponse:
    """
    List all available preset voices.
    
    Returns:
        List of available voices with descriptions and IDs.
    """
    try:
        voices = tts_service.list_voices()
        voice_list = [{"description": desc, "voice_id": voice_id} for desc, voice_id in voices]
        
        return JSONResponse(
            content={
                "success": True,
                "count": len(voice_list),
                "voices": voice_list
            }
        )
    except Exception as e:
        logger.error(f"Failed to list voices: {e}")
        return JSONResponse(
            content={
                "success": False,
                "error": str(e)
            },
            status_code=500
        )


@router.post("/synthesize", response_model=TTSResponse, summary="Synthesize speech from text")
async def synthesize(request: TTSRequest) -> JSONResponse:
    """
    Synthesize speech from text using the specified voice.
    
    Args:
        text: The text to convert to speech
        voice_id: Optional preset voice ID
        emotion: Emotion mode ('natural' or 'storytelling')
        
    Returns:
        Audio file path and metadata
    """
    try:        
        audio_path = tts_service.synthesize(
            text=request.text,
            voice_id=None,
            output_dir=request.output_dir
        )
        
        return JSONResponse(
            content={
                "success": True,
                "message": "Speech synthesized successfully",
                "audio_file": audio_path,
                "voice_id": request.voice_id or "default"
            }
        )
    except HTTPException as he:
        # Let FastAPI handle HTTP exceptions
        raise he
    except Exception as e:
        logger.exception(f"TTS synthesis failed: {e}")
        return JSONResponse(
            content={
                "success": False,
                "error": str(e)
            },
            status_code=500
        )


@router.post("/generate", summary="Synthesize and return audio file directly")
async def generate_audio(request: TTSRequest) -> FileResponse:
    """
    Synthesize speech from text and return the WAV file directly in one request.
    
    Args:
        text: The text to convert to speech
        voice_id: Optional preset voice ID
        emotion: Emotion mode ('natural' or 'storytelling')
        
    Returns:
        Audio file as FileResponse
    """
    try:
        audio_path = tts_service.synthesize(
            text=request.text,
            voice_id=request.voice_id,
            output_dir=request.output_dir,
            self_clone=request.self_clone
        )
        
        if not audio_path or not os.path.exists(audio_path):
            raise HTTPException(status_code=500, detail="Failed to generate audio file")
            
        return FileResponse(
            path=audio_path, 
            media_type="audio/wav",
            filename=os.path.basename(audio_path)
        )
    except HTTPException as he:
        raise he
    except Exception as e:
        logger.exception(f"TTS generation failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/synthesize/voice-clone", response_model=TTSResponse, summary="Synthesize with voice cloning")
async def synthesize_voice_clone(
    text: str = Form(..., description="Text to convert to speech"),
    ref_audio: UploadFile = File(..., description="Reference audio file (3-5 seconds recommended)"),
    ref_text: str = Form(..., description="Transcription of reference audio"),
    output_dir: str = Form(default="outputs/tts", description="Directory to save output files")
) -> JSONResponse:
    """
    Synthesize speech using voice cloning from reference audio.
    
    Args:
        text: The text to convert to speech
        ref_audio: Upload a reference audio file (3-5 seconds recommended)
        ref_text: Transcription of the reference audio
        
    Returns:
        Audio file path and metadata
    """
    try:
        # Save uploaded reference audio temporarily
        file_ext = os.path.splitext(getattr(ref_audio, 'filename', ''))[1] or '.wav'
        temp_audio_path = os.path.join(tempfile.gettempdir(), f"clone_{uuid.uuid4().hex}{file_ext}")
        with open(temp_audio_path, "wb") as f:
            f.write(await ref_audio.read())
        
        # Clean up temp file after synthesis
        try:
            audio_path = tts_service.synthesize(
                text=text,
                ref_audio=temp_audio_path,
                ref_text=ref_text,
                output_dir=output_dir
            )
        finally:
            # Clean up temp file
            if os.path.exists(temp_audio_path):
                os.remove(temp_audio_path)
        
        return JSONResponse(
            content={
                "success": True,
                "message": "Speech synthesized with cloned voice",
                "audio_file": audio_path
            }
        )
    except HTTPException as he:
        # Let FastAPI handle HTTP exceptions
        raise he
    except Exception as e:
        logger.exception(f"TTS voice cloning failed: {e}")
        return JSONResponse(
            content={
                "success": False,
                "error": str(e)
            },
            status_code=500
        )


@router.post("/generate/voice-clone", summary="Synthesize with voice cloning and return audio directly")
async def generate_voice_clone(
    text: str = Form(..., description="Text to convert to speech"),
    ref_audio: UploadFile = File(..., description="Reference audio file (3-5 seconds recommended)"),
    ref_text: str = Form(..., description="Transcription of reference audio"),
    output_dir: str = Form(default="outputs/tts", description="Directory to save output files")
) -> FileResponse:
    """
    Synthesize speech using voice cloning and return the WAV file directly in one request.
    """
    try:
        # Save uploaded reference audio temporarily
        file_ext = os.path.splitext(getattr(ref_audio, 'filename', ''))[1] or '.wav'
        temp_audio_path = os.path.join(tempfile.gettempdir(), f"clone_{uuid.uuid4().hex}{file_ext}")
        with open(temp_audio_path, "wb") as f:
            f.write(await ref_audio.read())
        
        # Clean up temp file after synthesis
        try:
            audio_path = tts_service.synthesize(
                text=text,
                ref_audio=temp_audio_path,
                ref_text=ref_text,
                output_dir=output_dir
            )
            
            if not audio_path or not os.path.exists(audio_path):
                raise HTTPException(status_code=500, detail="Failed to generate audio file")
                
            return FileResponse(
                path=audio_path, 
                media_type="audio/wav",
                filename=os.path.basename(audio_path)
            )
        finally:
            # Clean up temp file
            if os.path.exists(temp_audio_path):
                os.remove(temp_audio_path)
    except HTTPException as he:
        raise he
    except Exception as e:
        logger.exception(f"TTS voice cloning generation failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/audio/{filename}", summary="Get generated audio file")
async def get_audio(filename: str) -> FileResponse:
    """
    Retrieve a generated audio file.
    
    Args:
        filename: Name of the audio file to retrieve
        
    Returns:
        Audio file as FileResponse
    """
    # Search in outputs/tts directory
    output_dir = Path("outputs/tts")
    audio_path = output_dir / filename
    
    if not audio_path.exists():
        # Also check the main outputs directory
        audio_path = Path("outputs") / filename
    
    if not audio_path.exists():
        raise HTTPException(status_code=404, detail=f"Audio file not found: {filename}")
    
    return FileResponse(path=audio_path, media_type="audio/wav")
