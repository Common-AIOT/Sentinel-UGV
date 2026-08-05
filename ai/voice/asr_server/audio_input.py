"""Bounded upload and audio-container validation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .config import Settings
from .errors import ServiceError

ALLOWED_SUFFIXES = frozenset({".wav", ".flac", ".ogg"})
CONTENT_TYPE_SUFFIX = {
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/flac": ".flac",
    "audio/x-flac": ".flac",
    "audio/ogg": ".ogg",
    "application/ogg": ".ogg",
}


@dataclass(frozen=True, slots=True)
class AudioMetadata:
    duration_seconds: float
    sample_rate: int
    channels: int


def choose_suffix(filename: str | None, content_type: str | None) -> str:
    media_type = (content_type or "").split(";", 1)[0].strip().lower()
    if media_type in CONTENT_TYPE_SUFFIX:
        return CONTENT_TYPE_SUFFIX[media_type]
    suffix = Path(filename or "").suffix.lower()
    if media_type in {"", "application/octet-stream"} and suffix in ALLOWED_SUFFIXES:
        return suffix
    raise ServiceError(
        415,
        "UNSUPPORTED_AUDIO_TYPE",
        "Only WAV, FLAC, and OGG audio are accepted.",
    )


def inspect_audio(path: Path, settings: Settings) -> AudioMetadata:
    try:
        import soundfile as sf

        info = sf.info(str(path))
    except Exception as exc:
        raise ServiceError(
            415,
            "INVALID_AUDIO",
            "The uploaded file is not a decodable audio container.",
        ) from exc

    if info.frames <= 0 or info.samplerate <= 0:
        raise ServiceError(400, "EMPTY_AUDIO", "The uploaded audio is empty.")
    if info.channels <= 0 or info.channels > 2:
        raise ServiceError(
            422,
            "UNSUPPORTED_CHANNEL_COUNT",
            "Audio must be mono or stereo.",
        )
    if not 8_000 <= info.samplerate <= 48_000:
        raise ServiceError(
            422,
            "UNSUPPORTED_SAMPLE_RATE",
            "Audio sample rate must be between 8 kHz and 48 kHz.",
        )

    duration = float(info.frames / info.samplerate)
    if duration > settings.max_audio_seconds:
        raise ServiceError(
            413,
            "AUDIO_TOO_LONG",
            f"Audio duration exceeds {settings.max_audio_seconds:g} seconds.",
        )
    return AudioMetadata(
        duration_seconds=duration,
        sample_rate=int(info.samplerate),
        channels=int(info.channels),
    )
