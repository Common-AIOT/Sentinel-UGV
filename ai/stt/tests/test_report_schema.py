import unittest
from unittest.mock import patch

from sentinel_voice.llm import extract, keyword_extract, llm_extract
from sentinel_voice.safety import (
    EXTRACTION_FIELDS,
    REPORT_FIELDS,
    coerce_report,
    report_defaults,
    risk_assessment,
)


class ReportSchemaTest(unittest.TestCase):
    def assert_schema(self, report):
        self.assertEqual(set(report), set(REPORT_FIELDS))
        self.assertEqual(report["responseScope"], "GROUP")
        self.assertIn(report["mobilityStatus"], {"YES", "NO", "UNKNOWN"})
        self.assertIn(
            report["urgentConditionReported"], {"YES", "NO", "UNKNOWN"}
        )

    def assert_extraction_schema(self, extraction):
        self.assertEqual(set(extraction), set(EXTRACTION_FIELDS))
        self.assertIn(extraction["mobilityStatus"], {"YES", "NO", "UNKNOWN"})
        self.assertIn(
            extraction["urgentConditionReported"], {"YES", "NO", "UNKNOWN"}
        )

    def test_defaults_are_safe_and_group_scoped(self):
        report = report_defaults()
        self.assert_schema(report)
        self.assertTrue(report["operatorReviewRequired"])
        self.assertIsNone(report["reportedResponsiveCount"])

    def test_invalid_types_and_enums_are_coerced(self):
        report = coerce_report(
            {
                "responseScope": "PERSON",
                "anyResponseDetected": "yes",
                "reportedResponsiveCount": -1,
                "countConfidence": 2.0,
                "mobilityStatus": "MAYBE",
                "urgentConditionReported": [],
                "operatorReviewRequired": "false",
                "terminationReason": "invented",
                "diagnosis": "골절",
            }
        )
        self.assert_schema(report)
        self.assertIsNone(report["anyResponseDetected"])
        self.assertIsNone(report["reportedResponsiveCount"])
        self.assertIsNone(report["countConfidence"])
        self.assertEqual(report["mobilityStatus"], "UNKNOWN")
        self.assertTrue(report["operatorReviewRequired"])
        self.assertNotIn("diagnosis", report)

    def test_keyword_fallback_uses_same_schema(self):
        report = keyword_extract(
            "여기 두 명이고 움직일 수 없어요. 심한 출혈이 있어요."
        )
        self.assert_extraction_schema(report)
        self.assertEqual(report["reportedResponsiveCount"], 2)
        self.assertEqual(report["mobilityStatus"], "NO")
        self.assertEqual(report["urgentConditionReported"], "YES")

    @patch("sentinel_voice.llm._gms")
    def test_gms_output_is_restricted_to_report_schema(self, gms):
        message = type(
            "Message",
            (),
            {
                "content": (
                    '{"reportedResponsiveCount":1,'
                    '"mobilityStatus":"YES",'
                    '"urgentConditionReported":"NO",'
                    '"diagnosis":"정상"}'
                )
            },
        )()
        choice = type("Choice", (), {"message": message})()
        gms.return_value.chat.completions.create.return_value = type(
            "Response", (), {"choices": [choice]}
        )()

        report = llm_extract("한 명이고 움직일 수 있어요")
        self.assert_extraction_schema(report)
        self.assertNotIn("diagnosis", report)

    @patch("sentinel_voice.llm.llm_extract", side_effect=TimeoutError)
    def test_gms_failure_switches_to_schema_compatible_fallback(self, _):
        report, source = extract("세 명이고 이동할 수 있어요")
        self.assertEqual(source, "FALLBACK")
        self.assert_extraction_schema(report)
        self.assertEqual(report["reportedResponsiveCount"], 3)

    def test_system_failure_never_becomes_immediate_risk(self):
        risk = risk_assessment(
            {
                "anyResponseDetected": None,
                "terminationReason": "AUDIO_DEVICE_ERROR",
            }
        )
        self.assertEqual(risk["riskLevel"], "UNKNOWN")
        self.assertIn("AUDIO_DEVICE_ERROR", risk["riskReasons"][0])
        self.assertTrue(risk["operatorReviewRequired"])

    def test_risk_result_contains_reason_and_rule_version(self):
        risk = risk_assessment(
            {
                "anyResponseDetected": True,
                "mobilityStatus": "NO",
                "urgentConditionReported": "UNKNOWN",
                "terminationReason": "NORMAL",
            }
        )
        self.assertEqual(risk["riskLevel"], "URGENT")
        self.assertEqual(
            risk["riskReasons"], ["자력 이동이 불가능하다고 발화함"]
        )
        self.assertEqual(risk["ruleVersion"], "voice-risk-v1.0")


if __name__ == "__main__":
    unittest.main()
