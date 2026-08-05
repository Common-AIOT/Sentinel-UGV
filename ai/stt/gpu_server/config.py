"""Environment-backed configuration for the GPU ASR service."""

from __future__ import annotations

import os
from dataclasses import dataclass

SUPPORTED_LANGUAGE_CODES = frozenset(
    {
        "ar",
        "cs",
        "da",
        "de",
        "el",
        "en",
        "es",
        "fa",
        "fi",
        "fil",
        "fr",
        "hi",
        "hu",
        "id",
        "it",
        "ja",
        "ko",
        "mk",
        "ms",
        "nl",
        "pl",
        "pt",
        "ro",
        "ru",
        "sv",
        "th",
        "tr",
        "vi",
        "yue",
        "zh",
    }
)


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class Settings:
    """Runtime settings.

    Physical GPU 3 is hidden behind ``CUDA_VISIBLE_DEVICES=3``. Model libraries
    therefore intentionally use logical ``cuda:0`` inside the process.
    """

    backend: str = "qwen"
    model_id: str = "Qwen/Qwen3-ASR-1.7B"
    api_key: str = ""
    allow_unauthenticated: bool = False
    cuda_visible_devices: str = "3"
    dtype: str = "bfloat16"
    attention_implementation: str | None = None
    max_audio_bytes: int = 16 * 1024 * 1024
    max_audio_seconds: float = 30.0
    max_concurrency: int = 1
    queue_timeout_seconds: float = 0.05
    max_inference_batch_size: int = 4
    max_new_tokens: int = 256
    host: str = "127.0.0.1"
    port: int = 18100

    @classmethod
    def from_env(cls) -> Settings:
        backend = os.getenv("ASR_BACKEND", "qwen").strip().lower()
        default_model = (
            "Qwen/Qwen3-ASR-1.7B" if backend == "qwen" else "large-v3"
        )
        attention = os.getenv("ASR_ATTENTION_IMPLEMENTATION", "").strip() or None
        settings = cls(
            backend=backend,
            model_id=os.getenv("ASR_MODEL_ID", default_model).strip(),
            api_key=os.getenv("ASR_API_KEY", ""),
            allow_unauthenticated=_as_bool(
                os.getenv("ASR_ALLOW_UNAUTHENTICATED"), False
            ),
            cuda_visible_devices=os.getenv("ASR_CUDA_VISIBLE_DEVICES", "3").strip(),
            dtype=os.getenv("ASR_DTYPE", "bfloat16").strip(),
            attention_implementation=attention,
            max_audio_bytes=int(
                os.getenv("ASR_MAX_AUDIO_BYTES", str(16 * 1024 * 1024))
            ),
            max_audio_seconds=float(os.getenv("ASR_MAX_AUDIO_SECONDS", "30")),
            max_concurrency=int(os.getenv("ASR_MAX_CONCURRENCY", "1")),
            queue_timeout_seconds=float(os.getenv("ASR_QUEUE_TIMEOUT_SECONDS", "0.05")),
            max_inference_batch_size=int(
                os.getenv("ASR_MAX_INFERENCE_BATCH_SIZE", "4")
            ),
            max_new_tokens=int(os.getenv("ASR_MAX_NEW_TOKENS", "256")),
            host=os.getenv("ASR_HOST", "127.0.0.1").strip(),
            port=int(os.getenv("ASR_PORT", "18100")),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        if self.backend not in {"qwen", "whisper"}:
            raise ValueError("ASR_BACKEND must be 'qwen' or 'whisper'")
        if not self.model_id:
            raise ValueError("ASR_MODEL_ID must not be empty")
        if not self.cuda_visible_devices:
            raise ValueError("ASR_CUDA_VISIBLE_DEVICES must not be empty")
        if self.max_audio_bytes <= 0 or self.max_audio_seconds <= 0:
            raise ValueError("audio limits must be positive")
        if self.max_concurrency <= 0 or self.max_inference_batch_size <= 0:
            raise ValueError("concurrency limits must be positive")
        if self.queue_timeout_seconds <= 0:
            raise ValueError("ASR_QUEUE_TIMEOUT_SECONDS must be positive")
        if not (1 <= self.port <= 65535):
            raise ValueError("ASR_PORT must be between 1 and 65535")

    @property
    def auth_configured(self) -> bool:
        return self.allow_unauthenticated or bool(self.api_key)
