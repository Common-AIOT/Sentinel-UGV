"""KsponSpeech(AI Hub 「한국어 음성」)를 STT 평가에 쓸 수 있는 형태로 바꾼다.

S15P11A301-232. 측정과 판정은 S15P11A301-120이 한다.

    python -m tools.kspon --sample                       규칙 예시로 동작 확인
    python -m tools.kspon --transcript <파일>            실제 전사 파일을 정규화해 본다
    python -m tools.kspon --audit <파일>                 처리하지 못한 표기를 찾는다
    python -m tools.kspon --score <정답> <가설>          CER·WER 한 쌍만 계산

## 왜 필요한가

120의 "STT WER·핵심 표현 보존율"이 미측정으로 남은 이유는 **정답 전사가 붙은 음성이
없어서**였다(문서 §9-4). 직접 녹음하면 화자 확보와 전사가 모두 우리 부담인데,
그 정답 전사를 대량으로 제공하는 것이 공개 음성 코퍼스의 존재 이유다.

기존 세션 녹음으로는 대체할 수 없다. `session.jsonl`의 `sttText`는 STT가 **들은**
값이므로 그것을 정답으로 쓰면 오류율이 0으로 나온다(자기 답 채점).

## 그대로 쓸 수 없는 이유 — 전사가 ETRI 규칙이다

    b/ (70%)/(칠 십 퍼센트) 확률이라니 아/ (뭐+ 뭔)/(모+ 몬) 소리야

  - `b/` `n/` `o/` `l/` — 숨소리·소음·웃음 같은 비언어 표시
  - `(철자)/(발음)` — **이중 전사.** 둘 중 하나를 골라야 한다
  - `아/` 간투사, `+` 반복·말더듬, `*` 비속어 마스킹

철자를 고르면 `70% 확률이라니 뭐 소리야`, 발음을 고르면
`칠 십 퍼센트 확률이라니 모 소리야`가 된다.

**우리는 철자를 쓴다.** Whisper가 철자로 출력하기 때문이다. 대신 숫자 표기가
갈리면(`70%` vs `칠십 퍼센트`) 실제보다 나쁜 오류율이 나오므로, 정답과 STT 결과
**양쪽에 같은 정규화**를 적용한다. 목적은 국어학적으로 옳은 읽기가 아니라 **양쪽을
같은 규칙으로 맞추는 것**이다 — 한쪽만 변환하면 그 차이가 오류로 잡힌다.

그래서 고유어 수사도 한자어로 모은다. `3시`가 `삼시`가 되는데 STT가 `세 시`로
출력하면 `세시`로 남아 없는 오류가 잡힌다. 인원을 세는 "두 명"·"세 명"은 우리
도메인의 핵심이라 특히 그렇다. 조수사가 뒤따를 때만 바꾼다 — 조건 없이 바꾸면
"세상"의 '세'까지 건드린다.

남은 구멍: `스물세 살`처럼 **합성 고유어**는 변환하지 않는다. STT가 `23살`로 쓰면
어긋난다. 다만 이 어긋남은 오류율을 **실제보다 나쁘게** 만드는 방향이므로 결과를
낙관적으로 왜곡하지는 않는다. 우리 도메인 인원은 1~5명이라 여기서는 나타나지 않는다.

## 오디오는 헤더 없는 raw PCM이다

AI Hub 데이터 설명 그대로 16kHz · 16bit · little endian **linear PCM**이며 `.wav`가
아니다. soundfile로 바로 열리지 않아 `read_pcm()`을 둔다.

16kHz는 파이프라인 표준(`config.FS`)과 같아 리샘플이 필요 없다.

## 전사 파일은 EUC-KR이다

2018년 구축 데이터라 UTF-8이 아니다. UTF-8로 읽으면 깨진다.

## 처리하지 못한 표기를 조용히 넘기지 않는다

ETRI 규칙 전체를 이 파일이 다 안다고 가정하지 않는다. `--audit`이 정규화 후에도
남은 한글·숫자 아닌 문자를 세어 보여준다. 정답이 조용히 망가지면 측정값 전체가
쓸모없어지므로, 모르는 표기는 **드러내고** 규칙을 늘린다.
"""

from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path

import numpy as np

PCM_SAMPLE_RATE = 16_000
PCM_DTYPE = "<i2"  # little endian 16bit
TRANSCRIPT_ENCODING = "euc-kr"

# (철자)/(발음) 이중 전사. 괄호 안에 괄호가 없다고 보고 최단 일치로 잡는다.
_DUAL = re.compile(r"\(([^()]*)\)\s*/\s*\(([^()]*)\)")

# 이중 전사를 먼저 푼 뒤 남는 `/`는 모두 태그다 — `b/`, `아/` 처럼 바로 앞 토큰과
# 붙어 있다. 이중 전사 해소를 반드시 먼저 해야 한다(순서가 뒤바뀌면 발음 쪽이 태그로
# 오인되어 지워진다).
_TAG = re.compile(r"\S*/")

# 태그를 지운 뒤 남는 표기 기호. `+`는 반복·말더듬, `*`는 비속어 마스킹이다.
_MARKERS = re.compile(r"[+*#]")

_SPACES = re.compile(r"\s+")

# 채점 전에 지우는 문장부호. CER은 공백을 무시하므로 공백은 여기서 다루지 않는다.
_PUNCTUATION = re.compile(r"[.,!?~…\"'“”‘’·:;()\[\]{}<>「」『』/\\\-—]")

_SYMBOLS = {
    "%": "퍼센트",
    "&": "앤드",
    "@": "골뱅이",
    "+": "플러스",
    "=": "이퀄",
}

# 고유어 수사 → 한자어. 숫자 변환이 한자어로 나오므로 여기서 같은 형태로 모은다.
#
# 이것이 없으면 비대칭이 생긴다. `3시`는 `삼시`가 되는데 STT가 `세 시`로 출력하면
# `세시`로 남아 두 글자가 오류로 잡힌다 — 실제로는 같은 말이다. 인원을 세는
# "두 명"·"세 명"은 우리 도메인의 핵심이라 그냥 넘길 수 없다.
#
# 조수사가 뒤따를 때만 바꾼다. 조건 없이 바꾸면 "세상"의 '세'까지 건드린다.
_NATIVE_NUMBERS = {
    "하나": "일", "한": "일",
    "둘": "이", "두": "이",
    "셋": "삼", "세": "삼",
    "넷": "사", "네": "사",
    "다섯": "오", "여섯": "육", "일곱": "칠", "여덟": "팔", "아홉": "구",
    "열": "십", "스물": "이십", "스무": "이십",
}

# 긴 것을 먼저 둔다 — "하나"가 "한"보다 앞이어야 온전히 잡힌다.
_NATIVE_ALTERNATION = "|".join(
    sorted(_NATIVE_NUMBERS, key=len, reverse=True)
)
_COUNTERS = (
    "시간|시|분|초|명|사람|개|번|살|마리|권|장|층|병|잔|가지|군데|대|척|통"
)
# 뒤쪽에는 경계 조건을 두지 않는다. 한국어는 조수사 뒤에 조사가 바로 붙어
# (`3시에`, `3명이요`, `1번만`) 경계를 요구하면 정작 필요한 경우가 다 빠진다.
# 앞쪽만 막아 "스물세"·"세상"처럼 글자가 이어지는 경우를 걸러낸다.
_NATIVE_WITH_COUNTER = re.compile(
    rf"(?<![가-힣])({_NATIVE_ALTERNATION})\s*({_COUNTERS})"
)

_DIGITS = "영일이삼사오육칠팔구"
_SMALL_UNITS = ("", "십", "백", "천")
_BIG_UNITS = ("", "만", "억", "조")

_NUMBER = re.compile(r"\d+")


def _read_group(group: int) -> str:
    """0 < group < 10000 을 한자어로 읽는다."""
    out = []
    for position in range(3, -1, -1):
        digit = (group // 10**position) % 10
        if digit == 0:
            continue
        # 십·백·천 앞의 1은 생략한다 — "십일"이지 "일십일"이 아니다.
        if digit == 1 and position > 0:
            out.append(_SMALL_UNITS[position])
        else:
            out.append(_DIGITS[digit] + _SMALL_UNITS[position])
    return "".join(out)


def read_number(text: str) -> str:
    """숫자 문자열을 한자어 읽기로 바꾼다.

    정답과 STT 결과 양쪽에 같은 함수를 쓰는 것이 목적이다. 고유어 읽기(하나·둘)나
    조수사에 따른 변형은 다루지 않는다 — 한쪽만 맞추면 오히려 차이가 생긴다.
    """
    number = int(text)
    if number == 0:
        return "영"
    groups = []
    index = 0
    while number > 0:
        group = number % 10000
        if group:
            groups.append(_read_group(group) + _BIG_UNITS[index])
        number //= 10000
        index += 1
    return "".join(reversed(groups))


def clean_transcript(raw: str, *, pronunciation: bool = False) -> str:
    """ETRI 표기를 걷어내고 읽을 수 있는 한 줄로 만든다.

    `pronunciation=True`면 이중 전사에서 발음 쪽을 고른다. 기본은 철자다.
    """
    text = raw.strip()
    text = _DUAL.sub(lambda m: m.group(2 if pronunciation else 1), text)
    text = _TAG.sub(" ", text)
    text = _MARKERS.sub(" ", text)
    return _SPACES.sub(" ", text).strip()


def normalize_for_scoring(text: str) -> str:
    """채점 직전 정규화. 정답과 가설에 **똑같이** 적용해야 한다.

    한쪽만 적용하면 표기 차이가 그대로 오류로 잡혀 실제보다 나쁜 값이 나온다.
    """
    # 전각·호환 문자를 표준형으로 모은다(NFKC). "％" 같은 전각 기호가 섞여 있으면
    # 기호 표를 타지 못한다.
    text = unicodedata.normalize("NFKC", text)
    # 기호를 풀 때 앞뒤에 공백을 넣는다. `70%`를 `칠십퍼센트`로 붙여 버리면 STT가
    # 낸 `칠십 퍼센트`와 어절이 어긋나 WER만 부풀려진다(CER은 공백을 무시하므로
    # 영향이 없다).
    for symbol, reading in _SYMBOLS.items():
        text = text.replace(symbol, f" {reading} ")
    text = _NATIVE_WITH_COUNTER.sub(
        lambda m: _NATIVE_NUMBERS[m.group(1)] + m.group(2), text
    )
    text = _NUMBER.sub(lambda m: read_number(m.group()), text)
    text = _PUNCTUATION.sub(" ", text)
    return _SPACES.sub(" ", text).strip()


def _edit_distance(a: list, b: list) -> int:
    """Levenshtein 거리. jiwer가 환경에 없어 직접 계산한다."""
    if not a:
        return len(b)
    previous = list(range(len(b) + 1))
    for i, item_a in enumerate(a, start=1):
        current = [i]
        for j, item_b in enumerate(b, start=1):
            current.append(
                min(
                    previous[j] + 1,  # 삭제
                    current[j - 1] + 1,  # 삽입
                    previous[j - 1] + (item_a != item_b),  # 치환
                )
            )
        previous = current
    return previous[-1]


def cer(reference: str, hypothesis: str) -> float:
    """문자 오류율. 공백을 지우고 편집거리 / 기준 길이.

    잡음 제거 A/B 측정(`docs/measurements/잡음제거-실측.md`)과 **같은 정의**다.
    값을 나란히 놓고 비교하려면 정의가 같아야 한다.

    한국어는 띄어쓰기가 불안정해 WER보다 CER이 신뢰도가 높다. 판정은 CER을 주로 본다.
    """
    a = "".join(reference.split())
    b = "".join(hypothesis.split())
    if not a:
        return 0.0 if not b else 1.0
    return _edit_distance(list(a), list(b)) / len(a)


def wer(reference: str, hypothesis: str) -> float:
    """단어 오류율. 공백으로 자른 토큰 기준.

    띄어쓰기가 다르면 그것만으로 값이 올라간다. CER과 함께 읽어야 한다.
    """
    a = reference.split()
    b = hypothesis.split()
    if not a:
        return 0.0 if not b else 1.0
    return _edit_distance(a, b) / len(a)


def read_pcm(path: Path) -> np.ndarray:
    """헤더 없는 16bit little endian PCM을 [-1, 1) float32로 읽는다.

    **파일 길이가 홀수다 — 그것이 정상이다.** eval 6000개를 전수 확인한 결과
    6000개 전부 홀수 바이트였다(2026-08-04 실측). 손상이 아니라 체계적인 1바이트
    잉여이므로 예외로 막으면 데이터셋 전체를 쓸 수 없다.

    남는 1바이트가 앞(헤더)인지 뒤인지는 정렬을 좌우한다 — 틀리면 상·하위 바이트가
    뒤바뀌어 전 구간이 백색잡음이 된다. 실측으로 갈랐다:

        가정        RMS 평균   ZCR 평균   peak
        뒤 버림      0.023      0.080     정상
        앞 버림      0.533      0.278     전부 1.0 클리핑

    **뒤를 버리는 것이 맞다.** 실제 STT 대조로 재확인했다 —
    `약간 젊은 엄마 같은 느낌이야`가 CER 0.000으로 그대로 나왔다.
    """
    raw = Path(path).read_bytes()
    usable = len(raw) - (len(raw) % 2)
    return np.frombuffer(raw[:usable], dtype=PCM_DTYPE).astype(np.float32) / 32768.0


def read_transcript(path: Path) -> str:
    """전사 파일을 읽는다. UTF-8을 먼저 시도하고 EUC-KR로 물러난다.

    AI Hub 데이터 설명은 전사가 EUC-KR이라고 적어두었으나 **실제 `.trn` 파일은
    UTF-8이었다**(2026-08-04 실측 — EUC-KR 디코딩이 오히려 실패했다). 설명만 믿고
    EUC-KR로 고정하면 열리지 않는다. 배포본에 따라 다를 수 있으니 둘 다 시도한다.

    둘 다 실패하면 그대로 올린다. 조용히 대체 문자로 채우면 정답이 망가진 채
    측정에 들어간다.
    """
    raw = Path(path).read_bytes()
    for encoding in ("utf-8", TRANSCRIPT_ENCODING):
        try:
            return raw.decode(encoding).strip()
        except UnicodeDecodeError:
            continue
    raise UnicodeDecodeError(
        "kspon", raw[:32], 0, 1, f"UTF-8도 EUC-KR도 아니다: {path}"
    )


def parse_trn(path: Path) -> list[tuple[str, str]]:
    """`.trn` 목록을 (상대경로, 원본 전사) 쌍으로 읽는다.

    형식은 `KsponSpeech_eval/eval_clean/KsponSpeech_E00001.pcm :: 어/ 일단은 …` 이다.

    `scripts.zip`의 `eval_clean.trn`·`eval_other.trn`이 **공식 평가 셋**이다. 이것을
    쓰면 부분집합을 임의로 고르지 않아도 되므로 선정 편향이 없다.
    """
    rows = []
    for line in read_transcript(path).splitlines():
        if not line.strip():
            continue
        relative, separator, raw = line.partition(" :: ")
        if not separator:
            raise ValueError(f"' :: ' 구분자가 없다: {line[:60]}")
        rows.append((relative.strip(), raw))
    return rows


def has_latin(text: str) -> bool:
    """라틴 문자가 남아 있는가.

    `SRT`·`VIP` 같은 약어와 영어 낱말이다. 전사 철자 쪽은 라틴으로 두고 발음 쪽만
    한글로 적어(`(SRT)/(에스알티)`) STT가 어느 쪽으로 낼지 정해지지 않는다. 평가
    부분집합에서 빼는 근거로 쓴다 — eval 6000개 중 67개(1.1%)다.
    """
    return bool(re.search(r"[A-Za-z]", text))


def audit(text: str) -> Counter:
    """정규화 후에도 남은 **표기 기호**를 센다.

    비어 있으면 아는 표기만 나온 것이다. 무언가 남으면 규칙을 늘려야 한다.

    라틴 문자는 세지 않는다 — `SRT`·`VIP` 같은 약어는 처리하지 못한 표기가 아니라
    내용이고, STT도 라틴으로 출력하므로 남는 것이 정상이다. 라틴이 든 발화를 따로
    가리려면 `has_latin()`을 쓴다. eval 6000개를 이 기준으로 검사했을 때 라틴 외에
    남는 문자는 없었다(2026-08-04 실측).
    """
    cleaned = normalize_for_scoring(clean_transcript(text))
    return Counter(
        ch
        for ch in cleaned
        if not ch.isspace()
        and not ("가" <= ch <= "힣")
        and not ("ㄱ" <= ch <= "ㅣ")
        and not (ch.isascii() and ch.isalpha())
    )


SAMPLES = (
    # AI Hub·전처리 문서에 실린 표기 예시
    "b/ (70%)/(칠 십 퍼센트) 확률이라니 아/ (뭐+ 뭔)/(모+ 몬) 소리야",
    "n/ 그래서 어/ 내가 그때 (3시)/(세 시)에 갔는데",
    "l/ 아니 그게 아니라니까 o/",
)


def _print_samples() -> None:
    for raw in SAMPLES:
        spelling = clean_transcript(raw)
        sound = clean_transcript(raw, pronunciation=True)
        print(f"원본   {raw}")
        print(f"철자   {spelling}")
        print(f"발음   {sound}")
        print(f"채점용 {normalize_for_scoring(spelling)}")
        leftover = audit(raw)
        print(f"미처리 {dict(leftover) if leftover else '없음'}")
        print()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sample", action="store_true", help="규칙 예시 출력")
    parser.add_argument("--transcript", type=Path, help="전사 파일을 정규화해 본다")
    parser.add_argument("--audit", type=Path, help="처리하지 못한 표기를 집계한다")
    parser.add_argument(
        "--score", nargs=2, metavar=("정답", "가설"), help="CER·WER 계산"
    )
    args = parser.parse_args()

    if args.sample:
        _print_samples()
        return 0

    if args.score:
        reference = normalize_for_scoring(args.score[0])
        hypothesis = normalize_for_scoring(args.score[1])
        print(f"정답   {reference}")
        print(f"가설   {hypothesis}")
        print(f"CER    {cer(reference, hypothesis):.4f}")
        print(f"WER    {wer(reference, hypothesis):.4f}")
        return 0

    if args.transcript:
        for line in read_transcript(args.transcript).splitlines():
            if line.strip():
                print(normalize_for_scoring(clean_transcript(line)))
        return 0

    if args.audit:
        total = Counter()
        lines = 0
        for line in read_transcript(args.audit).splitlines():
            if line.strip():
                total += audit(line)
                lines += 1
        print(f"{lines}줄 검사")
        if not total:
            print("미처리 표기 없음 — 아는 규칙으로 모두 정리됐다")
            return 0
        print("미처리 문자 (많은 순):")
        for ch, count in total.most_common():
            print(f"  {ch!r}  {count}")
        print()
        print("이 문자들이 정답에 남으면 오류율이 부풀려진다. 규칙을 늘려야 한다.")
        return 1

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
