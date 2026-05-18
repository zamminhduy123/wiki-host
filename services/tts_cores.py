"""
Library-specific TTS backends.

TTSService depends on this module's small core interface instead of directly
depending on a concrete TTS library. To add another engine later, implement a
new core with synthesize(), save(), list_voices(), and get_voice_data().
"""

from __future__ import annotations

import logging
import os
import pickle
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import soundfile as sf

logger = logging.getLogger(__name__)

SERVICE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SERVICE_DIR.parent


@dataclass(frozen=True)
class TTSCoreConfig:
    backend: str = os.getenv("TTS_BACKEND", "omnivoice")
    default_voice_id: str = os.getenv("TTS_DEFAULT_VOICE_ID", "Binh")
    max_chars_per_chunk: int = int(os.getenv("TTS_MAX_CHARS_PER_CHUNK", "256"))
    max_ref_code_tokens: int = int(os.getenv("TTS_MAX_REF_CODE_TOKENS", "250"))
    vieneu_codec_repo: str = os.getenv("TTS_VIENEU_CODEC_REPO", "neuphonic/neucodec-onnx-decoder-int8")
    omnivoice_model_id: str = os.getenv("OMNIVOICE_MODEL_ID", "splendor1811/omnivoice-vietnamese")
    omnivoice_device: str = os.getenv("OMNIVOICE_DEVICE", "cuda:1")
    omnivoice_speed: float = float(os.getenv("OMNIVOICE_SPEED", "0.82"))
    omnivoice_ref_audio: Path = Path(
        os.getenv("OMNIVOICE_REF_AUDIO", str(PROJECT_ROOT / "data" / "voice" / "ref_voice.MP3"))
    )
    omnivoice_ref_text: Path = Path(
        os.getenv("OMNIVOICE_REF_TEXT", str(PROJECT_ROOT / "data" / "voice" / "ref_sub.txt"))
    )


class TTSCore(ABC):
    name: str
    sample_rate: int = 24000

    @abstractmethod
    def synthesize(
        self,
        text: str,
        voice_id: Optional[str] = None,
        ref_audio: Optional[str] = None,
        ref_text: Optional[str] = None,
        self_clone: bool = False,
    ):
        """Return a 1-D audio waveform."""

    def save(self, audio, output_path: str) -> None:
        sf.write(output_path, audio, self.sample_rate)

    def list_voices(self) -> list[tuple[str, str]]:
        return []

    def get_voice_data(self, voice_id: str) -> Optional[bytes]:
        return None


class OmniVoiceCore(TTSCore):
    name = "omnivoice"

    def __init__(self, config: TTSCoreConfig):
        self.config = config
        self.model = None
        self.default_prompt = None
        self._initialize()

    def _initialize(self) -> None:
        os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

        import torch
        from omnivoice import OmniVoice

        logger.info(
            "[TTS] Loading OmniVoice model %s on %s",
            self.config.omnivoice_model_id,
            self.config.omnivoice_device,
        )
        self.model = OmniVoice.from_pretrained(
            self.config.omnivoice_model_id,
            device_map=self.config.omnivoice_device,
            dtype=torch.float16,
        )
        self.sample_rate = int(getattr(self.model, "sampling_rate", 24000))

        if not self.config.omnivoice_ref_audio.exists():
            raise FileNotFoundError(f"OmniVoice reference audio not found: {self.config.omnivoice_ref_audio}")
        if not self.config.omnivoice_ref_text.exists():
            raise FileNotFoundError(f"OmniVoice reference text not found: {self.config.omnivoice_ref_text}")

        default_ref_text = self.config.omnivoice_ref_text.read_text(encoding="utf-8").strip()
        self.default_prompt = self.model.create_voice_clone_prompt(
            ref_audio=str(self.config.omnivoice_ref_audio),
            ref_text=default_ref_text,
        )
        logger.info("[TTS] OmniVoice initialized with reusable voice clone prompt")

    def synthesize(
        self,
        text: str,
        voice_id: Optional[str] = None,
        ref_audio: Optional[str] = None,
        ref_text: Optional[str] = None,
        self_clone: bool = False,
    ):
        if self.model is None:
            raise RuntimeError("OmniVoice model is not initialized")

        prompt = self.default_prompt
        if ref_audio:
            if not ref_text:
                raise ValueError("Voice cloning requires ref_text")
            if not os.path.exists(ref_audio):
                raise FileNotFoundError(f"Reference audio not found: {ref_audio}")
            prompt = self.model.create_voice_clone_prompt(
                ref_audio=str(ref_audio),
                ref_text=ref_text,
            )

        if voice_id:
            logger.warning("[TTS] OmniVoice core ignores preset voice_id=%s; using clone prompt", voice_id)

        audios = self.model.generate(
            text=text,
            language="vietnamese",
            voice_clone_prompt=prompt,
            speed=self.config.omnivoice_speed,
        )
        return audios[0]

    def list_voices(self) -> list[tuple[str, str]]:
        return [("OmniVoice Vietnamese cloned voice", "omnivoice")]


class VieneuCore(TTSCore):
    name = "vieneu"

    def __init__(self, config: TTSCoreConfig):
        self.config = config
        self.tts = None
        self.ref_codes = None
        self.ref_sub = None
        self._initialize()

    def _initialize(self) -> None:
        from vieneu import Vieneu

        self.tts = Vieneu(
            codec_repo=self.config.vieneu_codec_repo,
            codec_device="cpu",
        )
        self.sample_rate = int(getattr(self.tts, "sample_rate", 24000))
        self.ref_codes = None
        self.ref_sub = None
        logger.info("[TTS] VieNeu initialized")

    def _trim_ref_codes(self, ref_codes):
        try:
            if len(ref_codes) > self.config.max_ref_code_tokens:
                logger.warning(
                    "[TTS] Reference audio is too long (%s codes). Using first %s codes.",
                    len(ref_codes),
                    self.config.max_ref_code_tokens,
                )
                return ref_codes[: self.config.max_ref_code_tokens]
        except TypeError:
            logger.warning("[TTS] Could not measure reference code length; using as-is.")
        return ref_codes

    def _trim_ref_text(self, ref_text: str, original_ref_code_count: Optional[int]) -> str:
        if not ref_text or not original_ref_code_count:
            return ref_text
        if original_ref_code_count <= self.config.max_ref_code_tokens:
            return ref_text.strip()

        char_limit = max(1, int(len(ref_text) * self.config.max_ref_code_tokens / original_ref_code_count))
        candidate = ref_text[:char_limit].strip()
        sentence_end = max(candidate.rfind("."), candidate.rfind("?"), candidate.rfind("!"))
        if sentence_end >= 20:
            candidate = candidate[: sentence_end + 1].strip()
        return candidate or ref_text.strip()

    def synthesize(
        self,
        text: str,
        voice_id: Optional[str] = None,
        ref_audio: Optional[str] = None,
        ref_text: Optional[str] = None,
        self_clone: bool = False,
    ):
        if self.tts is None:
            raise RuntimeError("VieNeu model is not initialized")

        if voice_id:
            voice_data = self.tts.get_preset_voice(voice_id)
            return self.tts.infer(text=text, voice=voice_data, max_chars=self.config.max_chars_per_chunk)

        if ref_audio:
            if not ref_text:
                raise ValueError("Voice cloning requires ref_text")
            if not os.path.exists(ref_audio):
                raise FileNotFoundError(f"Reference audio not found: {ref_audio}")
            ref_codes = self.tts.encode_reference(str(ref_audio))
            original_ref_code_count = len(ref_codes)
            ref_codes = self._trim_ref_codes(ref_codes)
            ref_text = self._trim_ref_text(ref_text, original_ref_code_count)
            return self.tts.infer(
                text=text,
                ref_codes=ref_codes,
                ref_text=ref_text,
                max_chars=self.config.max_chars_per_chunk,
            )

        if self_clone:
            ref_code_path = PROJECT_ROOT / "data" / "ref_codes.pkl"
            ref_sub_path = PROJECT_ROOT / "data" / "voice" / "ref_sub.txt"
            if self.ref_codes is None:
                with open(ref_code_path, "rb") as f:
                    self.ref_codes = pickle.load(f)
                self.ref_sub = ref_sub_path.read_text(encoding="utf-8").strip()
            return self.tts.infer(
                text=text,
                ref_codes=self.ref_codes,
                ref_text=self.ref_sub,
                max_chars=self.config.max_chars_per_chunk,
            )

        voice_data = self.tts.get_preset_voice(self.config.default_voice_id)
        return self.tts.infer(text=text, voice=voice_data, max_chars=self.config.max_chars_per_chunk)

    def save(self, audio, output_path: str) -> None:
        self.tts.save(audio, output_path)

    def list_voices(self) -> list[tuple[str, str]]:
        return self.tts.list_preset_voices() if self.tts is not None else []

    def get_voice_data(self, voice_id: str) -> Optional[bytes]:
        if self.tts is None:
            return None
        return self.tts.get_preset_voice(voice_id)


def create_tts_core(config: Optional[TTSCoreConfig] = None) -> TTSCore:
    config = config or TTSCoreConfig()
    backend = config.backend.lower().strip()

    if backend == "omnivoice":
        return OmniVoiceCore(config)
    if backend == "vieneu":
        return VieneuCore(config)

    raise ValueError(f"Unsupported TTS backend: {config.backend}")
