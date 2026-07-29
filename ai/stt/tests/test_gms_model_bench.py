import unittest

from bench.gms_model_bench import (
    Case,
    benchmark,
    call_anthropic_model,
    call_gemini_model,
    is_strict_extraction,
    percentile,
    provider_for_model,
    request_options,
    score_extraction,
    summarize,
    unwrap_json_code_fence,
)


class FakeHttpResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeHttpClient:
    def __init__(self, payload):
        self.payload = payload
        self.request = None

    def post(self, url, **kwargs):
        self.request = {"url": url, **kwargs}
        return FakeHttpResponse(self.payload)


class GmsModelBenchTest(unittest.TestCase):
    def test_percentile_interpolates(self):
        self.assertEqual(percentile([100, 200, 300], 50), 200)
        self.assertEqual(percentile([], 95), None)

    def test_score_preserves_unknown_and_detects_hallucination(self):
        score = score_extraction(
            {
                "reportedResponsiveCount": None,
                "mobilityStatus": "UNKNOWN",
                "urgentConditionReported": "UNKNOWN",
            },
            {
                "reportedResponsiveCount": 2,
                "mobilityStatus": "YES",
                "urgentConditionReported": "UNKNOWN",
            },
        )
        self.assertEqual(
            score["hallucinatedSlots"],
            ["reportedResponsiveCount", "mobilityStatus"],
        )
        self.assertFalse(score["allSlotsCorrect"])

    def test_score_marks_safety_critical_reversal(self):
        score = score_extraction(
            {
                "reportedResponsiveCount": None,
                "mobilityStatus": "NO",
                "urgentConditionReported": "YES",
            },
            {
                "reportedResponsiveCount": None,
                "mobilityStatus": "YES",
                "urgentConditionReported": "NO",
            },
        )
        self.assertTrue(score["criticalError"])

    def test_strict_schema_rejects_extra_fields_and_boolean_count(self):
        self.assertTrue(
            is_strict_extraction(
                {
                    "reportedResponsiveCount": 2,
                    "mobilityStatus": "NO",
                    "urgentConditionReported": "UNKNOWN",
                }
            )
        )
        self.assertFalse(
            is_strict_extraction(
                {
                    "reportedResponsiveCount": 2,
                    "mobilityStatus": "NO",
                    "urgentConditionReported": "UNKNOWN",
                    "riskLevel": "URGENT",
                }
            )
        )

    def test_strict_schema_rejects_zero_responsive_count(self):
        self.assertFalse(
            is_strict_extraction(
                {
                    "reportedResponsiveCount": 0,
                    "mobilityStatus": "UNKNOWN",
                    "urgentConditionReported": "UNKNOWN",
                }
            )
        )
        self.assertFalse(
            is_strict_extraction(
                {
                    "reportedResponsiveCount": True,
                    "mobilityStatus": "NO",
                    "urgentConditionReported": "UNKNOWN",
                }
            )
        )

    def test_json_code_fence_is_unwrapped_for_every_provider(self):
        content = (
            "```json\n"
            '{"reportedResponsiveCount":2,"mobilityStatus":"NO",'
            '"urgentConditionReported":"UNKNOWN"}'
            "\n```"
        )
        self.assertEqual(
            unwrap_json_code_fence(content),
            '{"reportedResponsiveCount":2,"mobilityStatus":"NO",'
            '"urgentConditionReported":"UNKNOWN"}',
        )

    def test_code_fence_with_explanation_is_not_unwrapped(self):
        content = (
            "추출 결과입니다.\n```json\n"
            '{"reportedResponsiveCount":2,"mobilityStatus":"NO",'
            '"urgentConditionReported":"UNKNOWN"}'
            "\n```"
        )
        self.assertEqual(unwrap_json_code_fence(content), content)

    def test_nested_code_fence_is_not_unwrapped(self):
        content = "```json\n{}\n```\n설명\n```"
        self.assertEqual(unwrap_json_code_fence(content), content)

    def test_reasoning_option_is_only_used_for_gpt5_family(self):
        self.assertEqual(
            request_options("gpt-5-nano"),
            {"reasoning_effort": "minimal"},
        )
        self.assertEqual(
            request_options("gpt-5.4-nano"),
            {"reasoning_effort": "none"},
        )
        self.assertEqual(request_options("gemini-3.5-flash"), {})
        self.assertEqual(request_options("claude-opus-4.8"), {})

    def test_provider_is_selected_from_model_family(self):
        self.assertEqual(provider_for_model("gpt-5-nano"), "openai")
        self.assertEqual(provider_for_model("gemini-3.5-flash"), "gemini")
        self.assertEqual(provider_for_model("claude-opus-4-8"), "anthropic")

    def test_anthropic_adapter_normalizes_content_and_usage(self):
        client = FakeHttpClient(
            {
                "content": [
                    {
                        "type": "text",
                        "text": (
                            '{"reportedResponsiveCount":2,'
                            '"mobilityStatus":"NO",'
                            '"urgentConditionReported":"UNKNOWN"}'
                        ),
                    }
                ],
                "usage": {"input_tokens": 100, "output_tokens": 20},
            }
        )
        extraction, response = call_anthropic_model(
            client, "claude-opus-4-8", "두 명이고 움직일 수 없어요."
        )
        self.assertEqual(extraction["reportedResponsiveCount"], 2)
        self.assertEqual(response.total_tokens, 120)
        self.assertIn("api.anthropic.com", client.request["url"])
        self.assertIn("x-api-key", client.request["headers"])

    def test_anthropic_adapter_accepts_json_code_fence(self):
        client = FakeHttpClient(
            {
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "```json\n"
                            '{"reportedResponsiveCount":2,'
                            '"mobilityStatus":"NO",'
                            '"urgentConditionReported":"UNKNOWN"}'
                            "\n```"
                        ),
                    }
                ],
                "usage": {"input_tokens": 100, "output_tokens": 20},
            }
        )
        extraction, _response = call_anthropic_model(
            client, "claude-haiku-4-5-20251001", "두 명이고 움직일 수 없어요."
        )
        self.assertEqual(extraction["reportedResponsiveCount"], 2)

    def test_gemini_adapter_normalizes_content_and_usage(self):
        client = FakeHttpClient(
            {
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {
                                    "text": (
                                        '{"reportedResponsiveCount":null,'
                                        '"mobilityStatus":"YES",'
                                        '"urgentConditionReported":"NO"}'
                                    )
                                }
                            ]
                        }
                    }
                ],
                "usageMetadata": {
                    "promptTokenCount": 90,
                    "candidatesTokenCount": 10,
                    "totalTokenCount": 100,
                },
            }
        )
        extraction, response = call_gemini_model(
            client, "gemini-3.5-flash", "이동할 수 있고 긴급 증상은 없어요."
        )
        self.assertEqual(extraction["mobilityStatus"], "YES")
        self.assertEqual(response.total_tokens, 100)
        self.assertIn("gemini-3.5-flash:generateContent", client.request["url"])
        self.assertIn("x-goog-api-key", client.request["headers"])
        self.assertEqual(
            client.request["json"]["generationConfig"]["thinkingConfig"],
            {"thinkingBudget": 0},
        )

    def test_malformed_provider_json_is_preserved_for_evidence(self):
        client = FakeHttpClient(
            {
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {
                                    "text": (
                                        '{"reportedResponsiveCount":null,'
                                        '"mobilityStatus":"UNKNOWN"'
                                    )
                                }
                            ]
                        }
                    }
                ],
                "usageMetadata": {},
            }
        )
        with self.assertRaises(Exception) as caught:
            call_gemini_model(client, "gemini-3.5-flash", "테스트")
        self.assertIn("reportedResponsiveCount", caught.exception.raw_content)
        self.assertIsNotNone(caught.exception.normalized_response)

    def test_benchmark_records_success_and_failure_without_stopping(self):
        cases = [
            Case(
                "case-1",
                "두 명입니다.",
                {
                    "reportedResponsiveCount": 2,
                    "mobilityStatus": "UNKNOWN",
                    "urgentConditionReported": "UNKNOWN",
                },
                (),
            )
        ]

        def invoke(model, _text):
            if model == "broken":
                raise RuntimeError("API failed")
            return cases[0].expected, type("Response", (), {"usage": None})()

        rows = benchmark(
            cases=cases,
            models=["working", "broken"],
            runs=1,
            invoke=invoke,
        )
        self.assertTrue(rows[0]["allSlotsCorrect"])
        self.assertFalse(rows[1]["success"])
        self.assertEqual(rows[1]["errorType"], "RuntimeError")

    def test_summary_calculates_quality_and_latency(self):
        cases = [
            Case(
                "case-1",
                "움직일 수 없습니다.",
                {
                    "reportedResponsiveCount": None,
                    "mobilityStatus": "NO",
                    "urgentConditionReported": "UNKNOWN",
                },
                (),
            )
        ]

        def invoke(_model, _text):
            return cases[0].expected, type("Response", (), {"usage": None})()

        rows = benchmark(cases=cases, models=["model-a"], runs=2, invoke=invoke)
        summary = summarize(rows)[0]
        self.assertEqual(summary["successRatePct"], 100.0)
        self.assertEqual(summary["slotAccuracyPct"], 100.0)
        self.assertEqual(summary["exactMatchPct"], 100.0)
        self.assertEqual(summary["outputConsistencyPct"], 100.0)
        self.assertEqual(summary["criticalErrorCount"], 0)

    def test_summary_keeps_usage_from_schema_failure(self):
        rows = [
            {
                "model": "model-a",
                "caseId": "case-1",
                "success": False,
                "schemaValid": False,
                "elapsedMs": 100.0,
                "totalTokens": 321,
                "correctSlots": 0,
                "allSlotsCorrect": False,
                "hallucinatedSlots": [],
                "criticalError": False,
                "actual": None,
            }
        ]
        summary = summarize(rows)[0]
        self.assertEqual(summary["averageTotalTokens"], 321.0)


if __name__ == "__main__":
    unittest.main()
