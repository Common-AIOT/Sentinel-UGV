"""Qwen3-ASR model adapter for the GPU service."""

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


def create_backend(settings: Settings) -> ASRBackend:
    return QwenBackend(settings)
