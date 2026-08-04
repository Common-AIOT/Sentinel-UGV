"""KsponSpeech 전사 정규화와 채점 함수 (S15P11A301-232).

데이터셋이 없어도 검증할 수 있는 부분을 고정한다. 전사 규칙 처리와 오류율 계산은
순수 텍스트 변환이므로 AI Hub 승인과 무관하게 지금 확인할 수 있다.
"""

import unittest

import numpy as np

from tools.kspon import (
    audit,
    cer,
    clean_transcript,
    has_latin,
    normalize_for_scoring,
    parse_trn,
    read_number,
    read_pcm,
    wer,
)


class TranscriptCleaningTest(unittest.TestCase):
    RAW = "b/ (70%)/(칠 십 퍼센트) 확률이라니 아/ (뭐+ 뭔)/(모+ 몬) 소리야"

    def test_spelling_side_is_default(self):
        self.assertEqual(clean_transcript(self.RAW), "70% 확률이라니 뭐 뭔 소리야")

    def test_pronunciation_side_can_be_chosen(self):
        self.assertEqual(
            clean_transcript(self.RAW, pronunciation=True),
            "칠 십 퍼센트 확률이라니 모 몬 소리야",
        )

    def test_noise_labels_are_removed(self):
        for tag in ("b/", "n/", "o/", "l/"):
            with self.subTest(tag=tag):
                self.assertEqual(clean_transcript(f"{tag} 사람 있어요"), "사람 있어요")

    def test_filler_is_removed(self):
        self.assertEqual(clean_transcript("아/ 어/ 다쳤어요"), "다쳤어요")

    def test_dual_transcription_is_resolved_before_tag_removal(self):
        """순서가 뒤바뀌면 발음 쪽이 태그로 오인돼 지워진다.

        `(3시)/(세 시)`에서 `)/(`의 슬래시를 태그로 먼저 보면 `(3시)`까지 삭제된다.
        이중 전사 해소가 반드시 먼저다.
        """
        self.assertEqual(
            clean_transcript("어/ 내가 (3시)/(세 시)에 갔는데"), "내가 3시에 갔는데"
        )

    def test_markers_are_removed(self):
        self.assertEqual(clean_transcript("그 사람이 씨* 뭐라고"), "그 사람이 씨 뭐라고")

    def test_empty_input_is_empty_output(self):
        self.assertEqual(clean_transcript(""), "")
        self.assertEqual(clean_transcript("   "), "")


class NumberReadingTest(unittest.TestCase):
    def test_sino_korean_readings(self):
        cases = {
            "0": "영",
            "1": "일",
            "7": "칠",
            "10": "십",
            "11": "십일",
            "21": "이십일",
            "70": "칠십",
            "100": "백",
            "365": "삼백육십오",
            "1000": "천",
            "2026": "이천이십육",
        }
        for digits, expected in cases.items():
            with self.subTest(digits=digits):
                self.assertEqual(read_number(digits), expected)

    def test_one_is_dropped_before_units(self):
        """십·백·천 앞의 1은 생략한다 — "십일"이지 "일십일"이 아니다."""
        self.assertEqual(read_number("11"), "십일")
        self.assertNotIn("일십", read_number("11"))

    def test_ten_thousand_group(self):
        self.assertEqual(read_number("10000"), "일만")
        self.assertEqual(read_number("12345"), "일만이천삼백사십오")


class ScoringNormalizationTest(unittest.TestCase):
    def test_digits_and_symbols_become_korean(self):
        # 기호는 어절 경계를 지키려고 공백과 함께 풀린다.
        self.assertEqual(normalize_for_scoring("70%"), "칠십 퍼센트")

    def test_same_meaning_written_two_ways_matches(self):
        """정답과 STT 결과의 표기가 달라도 같은 값이면 오류가 0이어야 한다.

        이 정규화가 없으면 `70%` vs `칠십 퍼센트`가 전부 오류로 잡혀 실제보다
        나쁜 오류율이 나온다. 이것이 양쪽에 같은 함수를 적용하는 이유다.
        """
        reference = normalize_for_scoring("70% 확률입니다")
        hypothesis = normalize_for_scoring("칠십 퍼센트 확률입니다")
        self.assertEqual(cer(reference, hypothesis), 0.0)

    def test_punctuation_is_removed(self):
        self.assertEqual(normalize_for_scoring("다쳤어요, 아파요!"), "다쳤어요 아파요")

    def test_native_numeral_matches_digit_form(self):
        """고유어 수사와 숫자 표기가 같은 값이면 오류가 0이어야 한다.

        `3시`는 한자어로 `삼시`가 되는데 STT가 `세 시`로 출력하면 `세시`로 남아
        없는 오류가 잡힌다. 양쪽을 한자어로 모아 이 비대칭을 없앤다.
        """
        pairs = [
            ("3시에 만나요", "세 시에 만나요"),
            ("2명 있어요", "두 명 있어요"),
            ("3명이요", "세 명이요"),
            ("1번만 더", "한 번만 더"),
        ]
        for digits, native in pairs:
            with self.subTest(digits=digits):
                self.assertEqual(
                    cer(
                        normalize_for_scoring(digits),
                        normalize_for_scoring(native),
                    ),
                    0.0,
                )

    def test_native_numeral_needs_a_counter(self):
        """조수사가 없으면 건드리지 않는다. "세상"의 '세'를 바꾸면 말이 망가진다."""
        self.assertEqual(normalize_for_scoring("세상에"), "세상에")
        self.assertEqual(normalize_for_scoring("네가 그랬어"), "네가 그랬어")

    def test_symbol_expansion_keeps_word_boundary(self):
        """기호를 붙여 풀면 어절이 어긋나 WER만 부풀려진다.

        `70%` → `칠십퍼센트`로 붙이면 STT의 `칠십 퍼센트`와 단어 수가 달라진다.
        CER은 공백을 무시하므로 영향이 없지만 WER은 그대로 손해를 본다.
        """
        reference = normalize_for_scoring("70% 확률입니다")
        hypothesis = normalize_for_scoring("칠십 퍼센트 확률입니다")
        self.assertEqual(wer(reference, hypothesis), 0.0)

    def test_fullwidth_symbols_are_folded(self):
        """전각 기호가 섞여 있으면 기호 표를 타지 못한다. NFKC로 모은다."""
        self.assertEqual(normalize_for_scoring("７０％"), "칠십 퍼센트")


class ErrorRateTest(unittest.TestCase):
    def test_identical_text_is_zero(self):
        self.assertEqual(cer("다리를 다쳤어요", "다리를 다쳤어요"), 0.0)
        self.assertEqual(wer("다리를 다쳤어요", "다리를 다쳤어요"), 0.0)

    def test_cer_ignores_spacing_but_wer_does_not(self):
        """한국어 띄어쓰기는 불안정하다. CER을 주 지표로 두는 근거다."""
        self.assertEqual(cer("다리를 다쳤어요", "다리를다쳤어요"), 0.0)
        self.assertGreater(wer("다리를 다쳤어요", "다리를다쳤어요"), 0.0)

    def test_single_substitution(self):
        # "다리를 다쳤어요"(7자) 중 한 자만 다르다.
        self.assertAlmostEqual(cer("다리를 다쳤어요", "다리를 다쳤어유"), 1 / 7)

    def test_empty_reference(self):
        self.assertEqual(cer("", ""), 0.0)
        self.assertEqual(cer("", "무언가"), 1.0)

    def test_complete_miss_is_capped_by_reference_length(self):
        """가설이 길어지면 오류율이 1을 넘을 수 있다 — 정의상 정상이다.

        환각으로 길게 뱉는 경우가 이에 해당한다. 1로 자르지 않는다. 자르면
        환각의 심각도가 사라진다.
        """
        self.assertGreater(cer("네", "도와주세요 다쳤어요 가스 화재"), 1.0)


class AuditTest(unittest.TestCase):
    def test_known_notation_leaves_nothing(self):
        self.assertEqual(audit("b/ (70%)/(칠 십 퍼센트) 확률이라니"), {})

    def test_unknown_notation_is_surfaced(self):
        """모르는 표기를 조용히 넘기지 않는다. 정답이 망가지면 측정 전체가 무의미하다."""
        leftover = audit("사람이 있어요 ★")
        self.assertIn("★", leftover)

    def test_latin_is_content_not_unhandled_notation(self):
        """약어는 처리 못 한 표기가 아니라 내용이다. STT도 라틴으로 출력한다."""
        self.assertEqual(audit("좀 (SRT)/(에스알티)를 타면서까지"), {})
        self.assertEqual(audit("이제 OT 갔었을 때"), {})

    def test_latin_can_still_be_detected_for_exclusion(self):
        """평가 부분집합에서 빼려면 가려낼 수단이 따로 있어야 한다."""
        self.assertTrue(has_latin("이제 OT 갔었을 때"))
        self.assertFalse(has_latin("약간 젊은 엄마 같은 느낌이야"))


class TrnParsingTest(unittest.TestCase):
    LINE = (
        "KsponSpeech_eval/eval_clean/KsponSpeech_E00001.pcm :: "
        "어/ 일단은 억지로 과장해서"
    )

    def write(self, text: str, encoding: str):
        import os
        import tempfile

        handle = tempfile.NamedTemporaryFile(suffix=".trn", delete=False)
        handle.write(text.encode(encoding))
        handle.close()
        self.addCleanup(lambda: os.unlink(handle.name))
        from pathlib import Path

        return Path(handle.name)

    def test_parses_path_and_transcript(self):
        rows = parse_trn(self.write(self.LINE, "utf-8"))
        self.assertEqual(len(rows), 1)
        path, raw = rows[0]
        self.assertEqual(
            path, "KsponSpeech_eval/eval_clean/KsponSpeech_E00001.pcm"
        )
        self.assertEqual(clean_transcript(raw), "일단은 억지로 과장해서")

    def test_utf8_is_read_even_though_aihub_documents_euckr(self):
        """AI Hub 설명은 EUC-KR이라 적었지만 실제 .trn은 UTF-8이었다(실측)."""
        rows = parse_trn(self.write(self.LINE, "utf-8"))
        self.assertEqual(len(rows), 1)

    def test_euckr_still_works(self):
        """배포본이 다를 수 있으니 EUC-KR도 읽어야 한다."""
        rows = parse_trn(self.write(self.LINE, "euc-kr"))
        self.assertEqual(len(rows), 1)

    def test_missing_separator_is_an_error(self):
        """구분자가 없으면 조용히 넘기지 않는다 — 정답이 비어 버린다."""
        with self.assertRaises(ValueError):
            parse_trn(self.write("구분자없는줄", "utf-8"))


class PcmReadingTest(unittest.TestCase):
    def test_roundtrip(self):
        original = np.array([0, 16384, -16384, 32767], dtype="<i2")
        path = self.make_temp(original.tobytes())
        got = read_pcm(path)
        self.assertEqual(got.dtype, np.float32)
        np.testing.assert_allclose(got, original.astype(np.float32) / 32768.0)

    def test_odd_length_drops_the_trailing_byte(self):
        """홀수 길이가 정상이다 — eval 6000개 전부 홀수였다(2026-08-04 실측).

        처음에는 예외로 막았는데 그러면 데이터셋 전체를 쓸 수 없다. 남는 1바이트를
        **뒤에서** 버리는 것이 맞다는 것은 RMS·ZCR 대조와 실제 STT로 확인했다 —
        앞을 버리면 바이트 정렬이 어긋나 전 구간이 백색잡음이 된다.
        """
        path = self.make_temp(np.array([0, 16384], dtype="<i2").tobytes() + b"\x7f")
        got = read_pcm(path)
        self.assertEqual(len(got), 2)
        np.testing.assert_allclose(got, [0.0, 16384 / 32768.0])

    def make_temp(self, payload: bytes):
        import tempfile

        handle = tempfile.NamedTemporaryFile(suffix=".pcm", delete=False)
        handle.write(payload)
        handle.close()
        self.addCleanup(lambda: __import__("os").unlink(handle.name))
        from pathlib import Path

        return Path(handle.name)


if __name__ == "__main__":
    unittest.main()
