import json
import tempfile
import unittest
from pathlib import Path

from evaluation.asr_shadow_bench import (
    Case,
    Endpoint,
    benchmark,
    error_rate,
    load_cases,
    parse_endpoint,
    percentile,
    predict_polarity,
    score,
    summarize,
    write_results,
)


class ASRShadowBenchTest(unittest.TestCase):
    def case(self, **overrides):
        values = {
            "case_id": "mobility-risk",
            "audio": Path("sample.wav"),
            "reference": "다리가 부러져서 못 움직여요",
            "language": "ko",
            "condition": "clean",
            "critical_term_groups": (("못", "불가"), ("움직",)),
            "expected_polarity": "risk",
            "risk_patterns": ("못 움직", "움직일 수 없"),
            "safe_patterns": ("움직일 수 있", "움직여요"),
        }
        values.update(overrides)
        return Case(**values)

    def test_error_rates_normalize_spacing_and_punctuation(self):
        self.assertEqual(0.0, error_rate("세 명이에요", "세명이에요!", characters=True))
        self.assertGreater(error_rate("세 명이에요", "네 명이에요", characters=True), 0)
        self.assertGreater(error_rate("세 명이에요", "세명이에요", characters=False), 0)

    def test_percentile_interpolates(self):
        self.assertEqual(2.5, percentile([1, 2, 3, 4], 50))
        self.assertIsNone(percentile([], 95))

    def test_endpoint_rejects_plain_remote_http(self):
        with self.assertRaisesRegex(Exception, "HTTPS"):
            parse_endpoint("gpu=http://10.0.0.2:18100")
        endpoint = parse_endpoint("gpu=http://127.0.0.1:18100/")
        self.assertEqual("gpu", endpoint.label)
        self.assertEqual("http://127.0.0.1:18100", endpoint.base_url)

    def test_load_cases_resolves_audio_and_rejects_duplicates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "voice.wav").write_bytes(b"RIFF")
            row = {
                "caseId": "one",
                "audio": "voice.wav",
                "transcript": "못 움직여요",
                "language": "ko",
                "condition": "clean",
                "criticalTermGroups": [["못", "불가"]],
                "expectedPolarity": "risk",
                "riskPatterns": ["못 움직"],
                "safePatterns": ["움직일 수 있"],
            }
            manifest = root / "manifest.jsonl"
            manifest.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
            cases = load_cases(manifest)
            self.assertEqual(("못", "불가"), cases[0].critical_term_groups[0])
            self.assertEqual((root / "voice.wav").resolve(), cases[0].audio)

            manifest.write_text(
                "\n".join(json.dumps(row, ensure_ascii=False) for _ in range(2)),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "duplicated"):
                load_cases(manifest)

    def test_risk_pattern_takes_precedence_over_nested_safe_phrase(self):
        case = self.case(
            risk_patterns=("움직일 수 없",),
            safe_patterns=("움직일 수 있",),
        )
        self.assertEqual("risk", predict_polarity(case, "움직일 수 없어요"))

    def test_score_flags_risk_to_safe_and_non_speech_hallucination(self):
        risk = score(self.case(), "움직일 수 있어요")
        self.assertTrue(risk["risk_to_safe"])
        self.assertEqual(1, risk["critical_groups_preserved"])

        silence = self.case(
            reference="",
            condition="silence",
            expected_polarity="unknown",
            critical_term_groups=(),
            risk_patterns=(),
            safe_patterns=(),
        )
        self.assertTrue(score(silence, "안녕하세요")["hallucinated_non_speech"])
        self.assertFalse(score(silence, "")["hallucinated_non_speech"])

    def test_benchmark_continues_after_one_failure(self):
        cases = [self.case(case_id="ok"), self.case(case_id="fail")]

        def invoke(_endpoint, case, _key, _timeout, _request_id):
            if case.case_id == "fail":
                raise RuntimeError("ASR_OVERLOADED")
            return {
                "text": case.reference,
                "language": "ko",
                "inference_ms": 100,
                "duration_seconds": 2,
            }

        rows = benchmark(
            cases,
            [Endpoint("qwen", "http://127.0.0.1:18100")],
            api_key="secret",
            runs=2,
            timeout_seconds=1,
            invoke=invoke,
        )
        self.assertEqual(4, len(rows))
        self.assertEqual(2, sum(row.ok for row in rows))
        self.assertEqual({"ASR_OVERLOADED"}, {row.error_code for row in rows if not row.ok})

    def test_summary_reports_safety_latency_and_rtf(self):
        hypotheses = {
            "risk": "움직일 수 있어요",
            "silence": "안녕하세요",
        }
        cases = [
            self.case(case_id="risk"),
            self.case(
                case_id="silence",
                reference="",
                condition="silence",
                expected_polarity="unknown",
                critical_term_groups=(),
                risk_patterns=(),
                safe_patterns=(),
            ),
        ]

        def invoke(_endpoint, case, _key, _timeout, _request_id):
            return {
                "text": hypotheses[case.case_id],
                "inference_ms": 200,
                "duration_seconds": 2,
            }

        observations = benchmark(
            cases,
            [Endpoint("model", "http://localhost:18100")],
            api_key="key",
            runs=1,
            timeout_seconds=1,
            invoke=invoke,
        )
        summary = summarize(observations)[0]
        self.assertEqual(1, summary["riskToSafe"])
        self.assertEqual(1, summary["nonSpeechHallucinations"])
        self.assertEqual(0.1, summary["rtfMean"])
        self.assertIsNotNone(summary["cerMicro"])
        self.assertIsNotNone(summary["werMicro"])
        self.assertIsNotNone(summary["latencyP95Ms"])

    def test_write_results_creates_json_and_csv(self):
        case = self.case()

        def invoke(_endpoint, _case, _key, _timeout, _request_id):
            return {"text": case.reference, "inference_ms": 50, "duration_seconds": 1}

        observations = benchmark(
            [case],
            [Endpoint("qwen", "http://localhost:18100")],
            api_key="key",
            runs=1,
            timeout_seconds=1,
            invoke=invoke,
        )
        summary = summarize(observations)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            write_results(output, observations, summary, Path("manifest.jsonl"))
            self.assertTrue((output / "asr-shadow-raw.json").is_file())
            self.assertTrue((output / "asr-shadow-summary.json").is_file())
            self.assertTrue((output / "asr-shadow-summary.csv").is_file())


if __name__ == "__main__":
    unittest.main()
