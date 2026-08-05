import json
import unittest
from unittest.mock import patch

from sentinel_voice.gms_resilience import (
    GmsFailureKind,
    call_with_limited_retry,
    classify_gms_error,
    probe_gms_endpoint,
)
from sentinel_voice.llm import extract_with_status


class HttpError(Exception):
    def __init__(self, status_code):
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


class GmsResilienceTest(unittest.TestCase):
    def test_missing_sdk_is_dependency_failure(self):
        failure = classify_gms_error(ModuleNotFoundError("No module named 'openai'"))
        self.assertEqual(failure.kind, GmsFailureKind.DEPENDENCY)
        self.assertFalse(failure.retryable)

    def test_auth_error_is_not_retried(self):
        calls = 0

        def operation():
            nonlocal calls
            calls += 1
            raise HttpError(401)

        value, attempts, failure = call_with_limited_retry(
            operation, max_attempts=3, sleeper=lambda _: None
        )
        self.assertIsNone(value)
        self.assertEqual(calls, 1)
        self.assertEqual(attempts, 1)
        self.assertEqual(failure.kind, GmsFailureKind.AUTH)

    def test_rate_limit_and_server_error_are_retried_once(self):
        for status, expected in ((429, GmsFailureKind.RATE_LIMIT), (503, GmsFailureKind.SERVER)):
            calls = []

            def operation():
                calls.append(status)
                raise HttpError(status)

            _, attempts, failure = call_with_limited_retry(
                operation, max_attempts=2, sleeper=lambda _: None
            )
            self.assertEqual(attempts, 2)
            self.assertEqual(len(calls), 2)
            self.assertEqual(failure.kind, expected)

    def test_invalid_json_is_not_retried(self):
        error = json.JSONDecodeError("invalid", "x", 0)
        failure = classify_gms_error(error)
        self.assertEqual(failure.kind, GmsFailureKind.INVALID_RESPONSE)
        self.assertFalse(failure.retryable)

    @patch("sentinel_voice.llm.config.GMS_RETRY_DELAY", 0)
    @patch("sentinel_voice.llm.config.GMS_MAX_ATTEMPTS", 2)
    @patch("sentinel_voice.llm.llm_extract", side_effect=TimeoutError)
    def test_timeout_falls_back_after_limited_retry(self, llm_extract):
        result = extract_with_status("세 명이고 움직일 수 있어요")
        self.assertEqual(result.source, "FALLBACK")
        self.assertEqual(result.attempts, 2)
        self.assertEqual(result.failure.kind, GmsFailureKind.TIMEOUT)
        self.assertEqual(result.extraction["reportedResponsiveCount"], 3)
        self.assertEqual(llm_extract.call_count, 2)

    @patch("sentinel_voice.gms_resilience.socket.create_connection")
    def test_probe_targets_gms_host_without_api_request(self, connection):
        connection.return_value.__enter__.return_value = object()
        self.assertTrue(
            probe_gms_endpoint(
                "https://gms.ssafy.io/gmsapi/api.openai.com/v1",
                timeout_seconds=1,
            )
        )
        connection.assert_called_once_with(("gms.ssafy.io", 443), timeout=1)


if __name__ == "__main__":
    unittest.main()
