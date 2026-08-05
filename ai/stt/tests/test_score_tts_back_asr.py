import unittest

from tools.score_tts_back_asr import normalize, score_record


class TtsBackAsrScoreTest(unittest.TestCase):
    def test_punctuation_and_spacing_do_not_fail(self):
        result = score_record(
            {
                "expected": "주변에 세 명이 있습니다.",
                "transcript": "주변에 세명이 있습니다",
                "criticalTokens": ["세", "명"],
            },
            0.25,
        )
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["cer"], 0.0)

    def test_missing_negation_is_a_hard_failure(self):
        result = score_record(
            {
                "expected": "There are no other people nearby.",
                "transcript": "There are other people nearby.",
                "criticalTokens": ["no"],
            },
            0.25,
        )
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["missingCriticalTokens"], ["no"])

    def test_normalization_keeps_hangul_ascii_and_numbers(self):
        self.assertEqual(normalize(" 3명, YES! 한글 "), "3명yes한글")


if __name__ == "__main__":
    unittest.main()
