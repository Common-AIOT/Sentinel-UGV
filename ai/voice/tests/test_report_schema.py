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

    def test_reported_injury_is_urgent_regardless_of_severity(self):
        """부상을 말하면 정도를 재지 않고 YES다 (2026-08-04 팀 결정).

        이전에는 중대한 출혈·호흡 이상만 YES로 봤다. 그러면 "다리를 다쳤어요"가
        UNKNOWN으로 관제에 올라가 구조대원이 쓸 정보가 없다. 판단이 갈리면
        알려야 하는 쪽을 고른다 — 최종 판단은 관제의 사람이 한다.
        """
        for text in ("다리를 다쳤어요", "팔이 부러졌어요", "너무 아파요", "피가 계속 나요"):
            with self.subTest(text=text):
                self.assertEqual(
                    keyword_extract(text)["urgentConditionReported"], "YES"
                )

    def test_denied_injury_stays_no(self):
        """부정 표현이 긴급으로 뒤집히지 않는다.

        완화하면서 '다친'이라는 글자만 보고 YES를 내면 "다친 곳은 없습니다"가
        정반대로 보고된다. 부정을 먼저 걸러야 한다.
        """
        for text in ("다친 곳은 없습니다", "괜찮아요 다친 곳 없어요"):
            with self.subTest(text=text):
                self.assertEqual(
                    keyword_extract(text)["urgentConditionReported"], "NO"
                )

    def test_partial_denial_keeps_remaining_symptom(self):
        """하나를 부정해도 다른 증상이 남으면 YES다."""
        self.assertEqual(
            keyword_extract("출혈은 없는데 숨쉬기가 힘들어요")[
                "urgentConditionReported"
            ],
            "YES",
        )

    def test_pain_blocking_movement_is_immobile(self):
        """통증 때문에 못 일어난다고 하면 이동 불가다 (2026-08-04 팀 결정).

        이전에는 "통증만으로 추측하지 않는다"로 UNKNOWN이었다.
        """
        self.assertEqual(
            keyword_extract("일어나려니까 너무 아파요")["mobilityStatus"], "NO"
        )

    def test_injury_alone_does_not_decide_mobility(self):
        """부상을 말했을 뿐 이동 언급이 없으면 UNKNOWN이다.

        완화가 여기까지 번지면 "다리를 다쳤어요"가 이동 불가로 굳는다.
        """
        self.assertEqual(
            keyword_extract("다리를 다쳤어요")["mobilityStatus"], "UNKNOWN"
        )

    def test_rhetorical_negation_decides_mobility(self):
        for text in (
            "다리 다쳤는데 움직일 수 있겠냐고요.",
            "이 상태로 어떻게 움직여요.",
        ):
            self.assertEqual(keyword_extract(text)["mobilityStatus"], "NO")

    def test_nearby_people_add_to_speaker(self):
        """주변 인원을 덧붙여 말하면 화자를 더한다 (2026-08-04 팀 결정)."""
        self.assertEqual(
            keyword_extract("두 명 더 있어요")["reportedResponsiveCount"], 3
        )
        self.assertEqual(
            keyword_extract("옆에 한 명 있어요")["reportedResponsiveCount"], 2
        )
        # 총인원을 말한 경우는 더하지 않는다.
        self.assertEqual(
            keyword_extract("저 포함해서 세 명이요")["reportedResponsiveCount"], 3
        )

    def test_pinned_counts_as_urgent(self):
        """끼임·압착도 긴급이다.

        GMS 실호출 대조에서 발견했다(2026-08-04) — `다리가 눌려서 못 움직여요`를
        GMS는 YES로, 폴백은 UNKNOWN으로 냈다. 폴백이 더 낮은 등급을 내면 GMS
        장애 시 과소보고가 되므로 폴백을 올려 맞췄다.
        """
        for text in ("다리가 눌려서 못 움직여요", "기둥에 깔렸어요", "문에 끼였어요"):
            with self.subTest(text=text):
                self.assertEqual(
                    keyword_extract(text)["urgentConditionReported"], "YES"
                )

    def test_unresponsive_companion_is_excluded_from_count(self):
        """대답을 못 한다고 명시된 사람은 응답 인원에서 뺀다."""
        self.assertEqual(
            keyword_extract("옆에 한 명 있는데 대답을 안 해요")[
                "reportedResponsiveCount"
            ],
            1,
        )

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
        kwargs = gms.return_value.chat.completions.create.call_args.kwargs
        self.assertEqual(kwargs["model"], "gpt-5.4-mini")
        self.assertEqual(kwargs["reasoning_effort"], "none")

    @patch("sentinel_voice.llm._gms")
    def test_gpt5_nano_override_uses_minimal_reasoning(self, gms):
        message = type(
            "Message",
            (),
            {
                "content": (
                    '{"reportedResponsiveCount":null,'
                    '"mobilityStatus":"UNKNOWN",'
                    '"urgentConditionReported":"UNKNOWN"}'
                )
            },
        )()
        choice = type("Choice", (), {"message": message})()
        gms.return_value.chat.completions.create.return_value = type(
            "Response", (), {"choices": [choice]}
        )()

        llm_extract("테스트", model="gpt-5-nano")
        kwargs = gms.return_value.chat.completions.create.call_args.kwargs
        self.assertEqual(kwargs["reasoning_effort"], "minimal")

    @patch("sentinel_voice.llm.llm_extract", side_effect=TimeoutError)
    def test_gms_failure_switches_to_schema_compatible_fallback(self, _):
        report, source = extract("세 명이고 이동할 수 있어요")
        self.assertEqual(source, "FALLBACK")
        self.assert_extraction_schema(report)
        self.assertEqual(report["reportedResponsiveCount"], 3)

    def test_keyword_fallback_includes_speaker_in_count(self):
        self.assertEqual(
            keyword_extract("저 말고 대답할 수 있는 사람은 아무도 없어요")[
                "reportedResponsiveCount"
            ],
            1,
        )
        self.assertEqual(
            keyword_extract("사람은 저밖에 없어요")[
                "reportedResponsiveCount"
            ],
            1,
        )

    def test_zero_responsive_count_is_rejected(self):
        report = coerce_report({"reportedResponsiveCount": 0})
        self.assertIsNone(report["reportedResponsiveCount"])
        self.assertEqual(report["reportedCountStatus"], "UNKNOWN")

    def test_system_failure_never_becomes_immediate_risk(self):
        risk = risk_assessment(
            {
                "anyResponseDetected": None,
                "terminationReason": "AUDIO_DEVICE_ERROR",
            }
        )
        self.assertEqual(risk["riskLevel"], "UNKNOWN")
        # 근거 순서는 고정하지 않는다. v1.1은 관찰 근거를 먼저 두고 종료 사유를
        # 뒤에 덧붙인다. 확인할 것은 실패 사유가 근거에 남는다는 사실이다.
        self.assertTrue(
            any("AUDIO_DEVICE_ERROR" in reason for reason in risk["riskReasons"]),
            risk["riskReasons"],
        )
        self.assertTrue(risk["operatorReviewRequired"])

    def test_device_failure_with_false_is_not_treated_as_no_response(self):
        """시스템 실패를 요구조자 무응답으로 바꾸지 않는다(명세 33-3).

        스키마 계약상 장치 실패는 anyResponseDetected=null 이어야 하지만,
        상위 계층 결함으로 false 가 함께 오면 IMMEDIATE 근거가 되어 버린다.
        """
        for termination in ("AUDIO_DEVICE_ERROR", "GMS_UNAVAILABLE"):
            with self.subTest(termination=termination):
                risk = risk_assessment(
                    {
                        "anyResponseDetected": False,
                        "terminationReason": termination,
                    }
                )
                self.assertEqual(risk["riskLevel"], "UNKNOWN")
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
        self.assertEqual(risk["ruleVersion"], "voice-risk-v1.1")


if __name__ == "__main__":
    unittest.main()
