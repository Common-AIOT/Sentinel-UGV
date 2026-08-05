from __future__ import annotations

import io
import os
import threading
import unittest
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi.testclient import TestClient
from gpu_server.app import create_app
from gpu_server.backends import Transcription
from gpu_server.config import Settings
from gpu_server.errors import BackendInferenceError


def wav_bytes(*, seconds: float = 0.1, sample_rate: int = 16_000) -> bytes:
    output = io.BytesIO()
    frames = max(1, int(seconds * sample_rate))
    with wave.open(output, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(sample_rate)
        writer.writeframes(b"\x01\x00" * frames)
    return output.getvalue()


class FakeBackend:
    name = "fake-asr"
    model_id = "fake/model"

    def __init__(self) -> None:
        self.loaded = False
        self.paths: list[Path] = []

    def load(self) -> None:
        self.loaded = True

    def transcribe(self, path: Path, language: str | None) -> Transcription:
        self.paths.append(path)
        return Transcription(text="저 혼자 있어요", language=language or "ko", confidence=0.97)

    def close(self) -> None:
        self.loaded = False


class FailingLoadBackend(FakeBackend):
    def load(self) -> None:
        raise RuntimeError("load failed")


class FailingInferenceBackend(FakeBackend):
    def transcribe(self, path: Path, language: str | None) -> Transcription:
        raise BackendInferenceError("inference failed")


class BlockingBackend(FakeBackend):
    def __init__(self) -> None:
        super().__init__()
        self.started = threading.Event()
        self.release = threading.Event()

    def transcribe(self, path: Path, language: str | None) -> Transcription:
        self.started.set()
        self.release.wait(timeout=2)
        return super().transcribe(path, language)


def settings(**overrides) -> Settings:
    values = {
        "backend": "qwen",
        "model_id": "Qwen/Qwen3-ASR-1.7B",
        "api_key": "test-secret",
        "max_audio_seconds": 1.0,
        "queue_timeout_seconds": 0.05,
    }
    values.update(overrides)
    return Settings(**values)


class GPUASRContractTest(unittest.TestCase):
    def test_health_and_authenticated_transcription(self):
        backend = FakeBackend()
        app = create_app(settings(), backend)
        with TestClient(app) as client:
            health = client.get("/health")
            self.assertEqual(200, health.status_code)
            self.assertTrue(health.json()["ready"])

            response = client.post(
                "/v1/asr",
                headers={
                    "Authorization": "Bearer test-secret",
                    "X-Request-ID": "encounter-123-count",
                },
                data={"language": "ko"},
                files={"audio": ("answer.wav", wav_bytes(), "audio/wav")},
            )

        self.assertEqual(200, response.status_code)
        body = response.json()
        self.assertEqual("v1", body["api_version"])
        self.assertEqual("encounter-123-count", body["request_id"])
        self.assertEqual("저 혼자 있어요", body["text"])
        self.assertEqual("ko", body["language"])
        self.assertEqual("no-store", response.headers["cache-control"])
        self.assertEqual(1, len(backend.paths))
        self.assertFalse(os.path.exists(backend.paths[0]))

    def test_rejects_missing_or_wrong_credentials(self):
        backend = FakeBackend()
        with TestClient(create_app(settings(), backend)) as client:
            missing = client.post(
                "/v1/asr", files={"audio": ("a.wav", wav_bytes(), "audio/wav")}
            )
            wrong = client.post(
                "/v1/asr",
                headers={"X-API-Key": "wrong"},
                files={"audio": ("a.wav", wav_bytes(), "audio/wav")},
            )
        self.assertEqual(401, missing.status_code)
        self.assertEqual("UNAUTHORIZED", missing.json()["error"]["code"])
        self.assertEqual(401, wrong.status_code)
        self.assertEqual([], backend.paths)

    def test_missing_server_key_is_fail_closed(self):
        app = create_app(settings(api_key=""), FakeBackend())
        with TestClient(app) as client:
            health = client.get("/health")
            response = client.post(
                "/v1/asr", files={"audio": ("a.wav", wav_bytes(), "audio/wav")}
            )
        self.assertFalse(health.json()["ready"])
        self.assertEqual("AUTH_NOT_CONFIGURED", health.json()["error_code"])
        self.assertEqual(503, response.status_code)
        self.assertEqual("AUTH_NOT_CONFIGURED", response.json()["error"]["code"])

    def test_rejects_oversized_invalid_and_unsupported_audio(self):
        headers = {"Authorization": "Bearer test-secret"}
        tiny_limit = settings(max_audio_bytes=32)
        with TestClient(create_app(tiny_limit, FakeBackend())) as client:
            oversized = client.post(
                "/v1/asr",
                headers=headers,
                files={"audio": ("a.wav", wav_bytes(), "audio/wav")},
            )
        self.assertEqual("AUDIO_TOO_LARGE", oversized.json()["error"]["code"])

        with TestClient(create_app(settings(), FakeBackend())) as client:
            invalid = client.post(
                "/v1/asr",
                headers=headers,
                files={"audio": ("a.wav", b"not-wave", "audio/wav")},
            )
            unsupported = client.post(
                "/v1/asr",
                headers=headers,
                files={"audio": ("a.mp3", b"id3", "audio/mpeg")},
            )
        self.assertEqual(415, invalid.status_code)
        self.assertEqual("INVALID_AUDIO", invalid.json()["error"]["code"])
        self.assertEqual(415, unsupported.status_code)
        self.assertEqual("UNSUPPORTED_AUDIO_TYPE", unsupported.json()["error"]["code"])

    def test_rejects_long_audio_and_unknown_language(self):
        headers = {"Authorization": "Bearer test-secret"}
        with TestClient(create_app(settings(max_audio_seconds=0.05), FakeBackend())) as client:
            too_long = client.post(
                "/v1/asr",
                headers=headers,
                files={"audio": ("a.wav", wav_bytes(seconds=0.1), "audio/wav")},
            )
        self.assertEqual("AUDIO_TOO_LONG", too_long.json()["error"]["code"])

        with TestClient(create_app(settings(), FakeBackend())) as client:
            language = client.post(
                "/v1/asr",
                headers=headers,
                data={"language": "xx"},
                files={"audio": ("a.wav", wav_bytes(), "audio/wav")},
            )
        self.assertEqual(422, language.status_code)
        self.assertEqual("UNSUPPORTED_LANGUAGE", language.json()["error"]["code"])

    def test_load_and_inference_failures_have_stable_codes(self):
        headers = {"Authorization": "Bearer test-secret"}
        with TestClient(create_app(settings(), FailingLoadBackend())) as client:
            health = client.get("/health")
            not_ready = client.post(
                "/v1/asr",
                headers=headers,
                files={"audio": ("a.wav", wav_bytes(), "audio/wav")},
            )
        self.assertEqual("MODEL_LOAD_FAILED", health.json()["error_code"])
        self.assertEqual("MODEL_NOT_READY", not_ready.json()["error"]["code"])

        with TestClient(create_app(settings(), FailingInferenceBackend())) as client:
            failed = client.post(
                "/v1/asr",
                headers=headers,
                files={"audio": ("a.wav", wav_bytes(), "audio/wav")},
            )
        self.assertEqual(503, failed.status_code)
        self.assertEqual("MODEL_INFERENCE_FAILED", failed.json()["error"]["code"])
        self.assertTrue(failed.json()["error"]["retryable"])

    def test_capacity_limit_returns_overloaded(self):
        backend = BlockingBackend()
        app = create_app(settings(queue_timeout_seconds=0.02), backend)
        headers = {"Authorization": "Bearer test-secret"}
        with TestClient(app) as client, ThreadPoolExecutor(max_workers=1) as pool:
            first = pool.submit(
                client.post,
                "/v1/asr",
                headers=headers,
                files={"audio": ("a.wav", wav_bytes(), "audio/wav")},
            )
            self.assertTrue(backend.started.wait(timeout=1))
            overloaded = client.post(
                "/v1/asr",
                headers=headers,
                files={"audio": ("b.wav", wav_bytes(), "audio/wav")},
            )
            backend.release.set()
            successful = first.result(timeout=2)

        self.assertEqual(429, overloaded.status_code)
        self.assertEqual("ASR_OVERLOADED", overloaded.json()["error"]["code"])
        self.assertTrue(overloaded.json()["error"]["retryable"])
        self.assertEqual(200, successful.status_code)


if __name__ == "__main__":
    unittest.main()
