"""Lazy model adapters for Qwen3-ASR and faster-whisper."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .config import Settings
from .errors import BackendInferenceError

LANGUAGE_NAME = {
    "ar": "Arabic",
    "cs": "Czech",
    "da": "Danish",
    "de": "German",
    "el": "Greek",
    "en": "English",
    "es": "Spanish",
    "fa": "Persian",
    "fi": "Finnish",
    "fil": "Filipino",
    "fr": "French",
    "hi": "Hindi",
    "hu": "Hungarian",
    "id": "Indonesian",
    "it": "Italian",
    "ja": "Japanese",
    "ko": "Korean",
    "mk": "Macedonian",
    "ms": "Malay",
    "nl": "Dutch",
    "pl": "Polish",
    "pt": "Portuguese",
    "ro": "Romanian",
    "ru": "Russian",
    "sv": "Swedish",
    "th": "Thai",
    "tr": "Turkish",
    "vi": "Vietnamese",
    "yue": "Cantonese",
    "zh": "Chinese",
}
LANGUAGE_CODE = {name.lower(): code for code, name in LANGUAGE_NAME.items()}


@dataclass(frozen=True, slots=True)
class Transcription:
    text: str
    language: str | None = None
    confidence: float | None = None


class ASRBackend(Protocol):
    name: str
    model_id: str

    def load(self) -> None: ...

    def transcribe(self, path: Path, language: str | None) -> Transcription: ...

    def close(self) -> None: ...


class QwenBackend:
    name = "qwen3-asr"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.model_id = settings.model_id
        self._model = None

    def load(self) -> None:
        import torch
        from qwen_asr import Qwen3ASRModel

        dtype = getattr(torch, self.settings.dtype, None)
        if dtype is None:
            raise ValueError(f"Unsupported torch dtype: {self.settings.dtype}")
        kwargs = {
            "dtype": dtype,
            "device_map": "cuda:0",
            "max_inference_batch_size": self.settings.max_inference_batch_size,
            "max_new_tokens": self.settings.max_new_tokens,
        }
        if self.settings.attention_implementation:
            kwargs["attn_implementation"] = self.settings.attention_implementation
        self._model = Qwen3ASRModel.from_pretrained(self.model_id, **kwargs)

    def transcribe(self, path: Path, language: str | None) -> Transcription:
        if self._model is None:
            raise BackendInferenceError("backend is not loaded")
        try:
            results = self._model.transcribe(
                audio=str(path),
                language=LANGUAGE_NAME.get(language) if language else None,
            )
            if not results:
                return Transcription(text="", language=language)
            result = results[0]
            detected = getattr(result, "language", None)
            normalized = LANGUAGE_CODE.get(str(detected).lower(), language)
            return Transcription(
                text=str(getattr(result, "text", "")).strip(),
                language=normalized,
            )
        except BackendInferenceError:
            raise
        except Exception as exc:
            raise BackendInferenceError("Qwen3-ASR inference failed") from exc

    def close(self) -> None:
        self._model = None


class WhisperBackend:
    name = "faster-whisper"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.model_id = settings.model_id
        self._model = None

    def load(self) -> None:
        from faster_whisper import WhisperModel

        compute_type = "float16" if self.settings.dtype in {"float16", "bfloat16"} else self.settings.dtype
        self._model = WhisperModel(
            self.model_id,
            device="cuda",
            compute_type=compute_type,
        )

    def transcribe(self, path: Path, language: str | None) -> Transcription:
        if self._model is None:
            raise BackendInferenceError("backend is not loaded")
        try:
            segments, info = self._model.transcribe(
                str(path),
                language=language,
                beam_size=5,
                condition_on_previous_text=False,
                vad_filter=False,
            )
            text = " ".join(segment.text.strip() for segment in segments).strip()
            confidence = getattr(info, "language_probability", None)
            return Transcription(
                text=text,
                language=getattr(info, "language", language),
                confidence=float(confidence) if confidence is not None else None,
            )
        except BackendInferenceError:
            raise
        except Exception as exc:
            raise BackendInferenceError("faster-whisper inference failed") from exc

    def close(self) -> None:
        self._model = None


def create_backend(settings: Settings) -> ASRBackend:
    if settings.backend == "qwen":
        return QwenBackend(settings)
    if settings.backend == "whisper":
        return WhisperBackend(settings)
    raise ValueError(f"Unsupported ASR backend: {settings.backend}")
