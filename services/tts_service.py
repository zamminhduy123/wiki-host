"""
tts_service.py - stable TTS service facade.

The public service API stays here. Concrete TTS libraries live behind cores in
services.tts_cores so the engine can be swapped without rewriting routers.
"""

from __future__ import annotations

import logging
import os
import uuid
from typing import Optional

from fastapi import HTTPException

from services.tts_cores import create_tts_core

logger = logging.getLogger(__name__)


class TTSService:
    """Stable service wrapper around a pluggable TTS core."""

    def __init__(self):
        self.core = None
        self.tts = None
        self._initialize()

    def _initialize(self) -> None:
        logger.info("[TTS] Initializing TTS service core")
        try:
            self.core = create_tts_core()
            # Backwards-compatible availability check used by notebooks/routes.
            self.tts = self.core
            logger.info("[TTS] Initialized %s core", self.core.name)
        except Exception as e:
            logger.exception("[TTS] Failed to initialize TTS core: %s", e)
            self.core = None
            self.tts = None

    def synthesize(
        self,
        text: str,
        voice_id: Optional[str] = None,
        ref_audio: Optional[str] = None,
        ref_text: Optional[str] = None,
        self_clone: bool = False,
        output_dir: str = "outputs/tts",
    ) -> Optional[str]:
        """
        Synthesize speech from text.

        Args:
            text: The text to convert to speech.
            voice_id: Optional preset voice ID. Some cores may ignore this.
            ref_audio: Optional path to reference audio for voice cloning.
            ref_text: Transcription of reference audio.
            self_clone: Whether to use the core's cached self-clone mode.
            output_dir: Directory to save output files.

        Returns:
            Path to generated audio file.
        """
        if self.core is None:
            raise HTTPException(status_code=503, detail="TTS service not available")

        try:
            os.makedirs(output_dir, exist_ok=True)
            audio = self.core.synthesize(
                text=text,
                voice_id=voice_id,
                ref_audio=ref_audio,
                ref_text=ref_text,
                self_clone=self_clone,
            )

            output_filename = f"tts_{uuid.uuid4().hex[:12]}.wav"
            output_path = os.path.join(output_dir, output_filename)
            self.core.save(audio, output_path)
            logger.info("[TTS] Generated audio saved to: %s", output_path)
            return output_path
        except HTTPException:
            raise
        except Exception as e:
            logger.exception("[TTS] Synthesis failed: %s", e)
            raise HTTPException(status_code=500, detail=f"TTS synthesis failed: {str(e)}")

    def list_voices(self) -> list[tuple[str, str]]:
        if self.core is None:
            return []
        try:
            return self.core.list_voices()
        except Exception as e:
            logger.error("[TTS] Failed to list voices: %s", e)
            return []

    def get_voice_data(self, voice_id: str) -> Optional[bytes]:
        if self.core is None:
            return None
        try:
            return self.core.get_voice_data(voice_id)
        except Exception as e:
            logger.error("[TTS] Failed to get voice data: %s", e)
            return None


# Global instance
tts_service = TTSService()
