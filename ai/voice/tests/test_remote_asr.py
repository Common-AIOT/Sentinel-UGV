"""Jetson 원격 ASR 클라이언트의 인증·재시도·오류 계약 검증."""

import unittest

import httpx
import numpy as np

from sentinel_voice.remote_asr import RemoteASRClient, RemoteASRError


def speech():
    return np.full(1600, 0.1, dtype=np.float32)


class RemoteASRClientTest(unittest.TestCase):
    def client(self, handler, **kwargs):
        transport = httpx.MockTransport(handler)
        injected = httpx.Client(transport=transport)
        return RemoteASRClient(
            base_url="https://asr.internal",
            api_key="test-secret",
            client=injected,
            sleep=lambda seconds: None,
            **kwargs,
        )

    def test_success_sends_auth_and_pcm_wav(self):
        observed = {}

        def handler(request):
            observed["authorization"] = request.headers["authorization"]
            observed["request_id"] = request.headers["x-request-id"]
            observed["content_type"] = request.headers["content-type"]
            observed["body"] = request.content
            return httpx.Response(200, json={"text": " 네 여기 있어요 "})

        client = self.client(handler)
        text, no_speech_prob = client.transcribe(speech(), sample_rate=16000)

        self.assertEqual(text, "네 여기 있어요")
        self.assertEqual(no_speech_prob, 0.0)
        self.assertEqual(observed["authorization"], "Bearer test-secret")
        self.assertTrue(observed["request_id"].startswith("jetson-"))
        self.assertIn("multipart/form-data", observed["content_type"])
        self.assertIn(b"RIFF", observed["body"])

    def test_health_requires_ready_server(self):
        client = self.client(
            lambda request: httpx.Response(
                200,
                json={
                    "status": "ok",
                    "ready": True,
                    "backend": "qwen3-asr",
                    "model": "Qwen/Qwen3-ASR-1.7B",
                    "cuda_visible_devices": "3",
                    "error_code": None,
                },
            )
        )

        health = client.health()

        self.assertTrue(health["ready"])
        self.assertEqual("qwen3-asr", health["backend"])

    def test_health_rejects_degraded_server(self):
        client = self.client(
            lambda request: httpx.Response(
                200,
                json={
                    "status": "degraded",
                    "ready": False,
                    "backend": "qwen3-asr",
                    "model": "Qwen/Qwen3-ASR-1.7B",
                    "cuda_visible_devices": "3",
                    "error_code": "MODEL_LOAD_FAILED",
                },
            ),
            max_attempts=1,
        )

        with self.assertRaises(RemoteASRError) as caught:
            client.health()

        self.assertEqual("MODEL_LOAD_FAILED", caught.exception.code)
        self.assertTrue(caught.exception.retryable)

    def test_health_transport_failure_is_stable(self):
        def handler(request):
            raise httpx.ConnectError("unreachable", request=request)

        client = self.client(handler, max_attempts=1)
        with self.assertRaises(RemoteASRError) as caught:
            client.health()

        self.assertEqual("ASR_UNAVAILABLE", caught.exception.code)
        self.assertTrue(caught.exception.retryable)

    def test_retryable_503_is_retried_once(self):
        calls = []

        def handler(request):
            calls.append(request.headers["x-request-id"])
            if len(calls) == 1:
                return httpx.Response(
                    503,
                    json={
                        "error": {
                            "code": "MODEL_NOT_READY",
                            "retryable": True,
                        }
                    },
                )
            return httpx.Response(200, json={"text": "두 명이에요"})

        client = self.client(handler, max_attempts=2)
        text, _ = client.transcribe(speech(), sample_rate=16000)

        self.assertEqual(text, "두 명이에요")
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0], calls[1])

    def test_non_retryable_auth_error_is_stable(self):
        def handler(request):
            return httpx.Response(
                401,
                json={
                    "error": {"code": "UNAUTHORIZED", "retryable": False}
                },
            )

        client = self.client(handler, max_attempts=2)
        with self.assertRaises(RemoteASRError) as caught:
            client.transcribe(speech(), sample_rate=16000)

        self.assertEqual(caught.exception.code, "UNAUTHORIZED")
        self.assertFalse(caught.exception.retryable)
        self.assertEqual(caught.exception.status_code, 401)

    def test_timeout_becomes_retryable_safe_error(self):
        def handler(request):
            raise httpx.ReadTimeout("timeout", request=request)

        client = self.client(handler, max_attempts=1)
        with self.assertRaises(RemoteASRError) as caught:
            client.transcribe(speech(), sample_rate=16000)

        self.assertEqual(caught.exception.code, "ASR_TIMEOUT")
        self.assertTrue(caught.exception.retryable)

    def test_invalid_success_body_is_rejected(self):
        client = self.client(
            lambda request: httpx.Response(200, json={"transcript": "wrong"}),
            max_attempts=1,
        )
        with self.assertRaises(RemoteASRError) as caught:
            client.transcribe(speech(), sample_rate=16000)
        self.assertEqual(caught.exception.code, "ASR_INVALID_RESPONSE")

    def test_plain_http_requires_explicit_development_override(self):
        with self.assertRaises(ValueError):
            RemoteASRClient(
                base_url="http://gpu.internal:18100",
                api_key="test-secret",
            )
        RemoteASRClient(
            base_url="http://127.0.0.1:18100",
            api_key="test-secret",
        ).close()


if __name__ == "__main__":
    unittest.main()
