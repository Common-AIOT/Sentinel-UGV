"""KsponSpeech 공식 평가 셋으로 STT 오류율을 측정한다 (S15P11A301-232 → 120).

    python -m tools.kspon_bench --root ~/kspon                     eval_clean 전량
    python -m tools.kspon_bench --root ~/kspon --limit 150          시드 추출로 빠르게
    python -m tools.kspon_bench --root ~/kspon --split eval_other
    python -m tools.kspon_bench --root ~/kspon --no-prompt-compare  프롬프트 비교 생략

## 데이터 준비

AI Hub 「한국어 음성」(KsponSpeech)에서 **두 개만** 받는다. 01~05(약 71GB)는 학습용이라
필요하지 않다 — 우리는 학습하지 않고 평가만 한다.

    KsponSpeech_eval.zip      536MB   평가용 음성
    KsponSpeech_scripts.zip    24MB   전사(정답) + 공식 평가 셋 목록

받은 파일은 `.part0` 확장자가 붙어 오므로 `.zip`으로 바꿔 풀고 아래 구조로 둔다.
음성 파일은 저장소에 두지 않는다.

    <root>/scripts/eval_clean.trn
    <root>/scripts/eval_other.trn
    <root>/eval/eval_clean/*.pcm
    <root>/eval/eval_other/*.pcm

## 왜 이 데이터인가

기존 STT 측정은 정답을 모델 출력에서 만들었다 — 정답을 세울 수 있었던 발화가 로컬이
맞힌 발화였다. 모델이 잘 푸는 문제로 모델을 채점한 순환이다. KsponSpeech는 정답이 우리
모델과 무관하게 붙어 있어 그 순환이 끊어진다.

`eval_clean.trn`·`eval_other.trn`은 **공식 평가 셋**이라 부분집합을 임의로 고르지 않는다.

## 무엇을 빼는가 — 반드시 기록한다

  - **라틴 문자가 남는 발화** — `SRT`·`VIP` 같은 약어는 전사 철자 쪽은 라틴, 발음 쪽은
    한글(`(SRT)/(에스알티)`)이라 STT가 어느 쪽으로 낼지 정해지지 않는다. 표기 차이가
    오류로 잡히므로 뺀다. eval_clean 3000개 중 24개(0.8%).
  - **`--min-chars`보다 짧은 발화** — `그래`(2자)를 `아 그래`로 들으면 그것만으로 CER
    0.5다. 발화별 평균에서 초단문이 과대 대표된다. 5자 기준에서 505개(16.8%)가 빠진다.

## 주 지표는 코퍼스 단위다

총 편집거리 ÷ 총 정답 길이. 발화별 오류율의 평균이 아니다 — 위의 초단문 문제 때문이다.
한국어는 띄어쓰기가 불안정해 CER을 주로 보고 WER은 함께 참고한다.

## 운영과 같은 설정으로 돌린다

`config.STT_DECODE`와 `audio.normalize`를 그대로 쓴다. 벤치 전용 설정으로 재면 그
숫자는 우리 시스템의 값이 아니다. 다르게 두는 것은 `initial_prompt` 하나뿐이며, 그것도
운영값과 없음을 **둘 다** 재서 나란히 보고한다.

## 이 숫자가 뜻하는 것

KsponSpeech는 일상 자유대화다. 따라서 **"우리 STT가 한국어 구어체를 얼마나 받아쓰는가"**
이고 **"재난 현장에서 얼마나 받아쓰는가"가 아니다.** 마이크·잔향·약한 발화·도메인 어휘는
여기 없다. 도메인 성능은 별도 녹음(`tools/record_corpus.py`)으로만 알 수 있다.
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path

import numpy as np
import soundfile as sf

from tools.kspon import (
    _edit_distance,
    cer,
    clean_transcript,
    has_latin,
    normalize_for_scoring,
    parse_trn,
    read_pcm,
    wer,
)

FS = 16_000
SEED = 20260804
TRN_PREFIX = "KsponSpeech_eval/"


def load_noise(path: Path) -> np.ndarray:
    data, rate = sf.read(str(path), dtype="float32", always_2d=False)
    if data.ndim > 1:
        data = data.mean(axis=1)
    if rate != FS:
        # 선형 보간으로 내린다. 소음은 위상 정밀도가 중요하지 않다.
        count = int(len(data) * FS / rate)
        data = np.interp(
            np.linspace(0, len(data) - 1, count), np.arange(len(data)), data
        ).astype(np.float32)
    return data


def fit(noise: np.ndarray, length: int, rng) -> np.ndarray:
    """소음을 필요한 길이로 맞춘다. 짧으면 이어 붙이고 길면 임의 구간을 자른다."""
    if len(noise) < length:
        noise = np.tile(noise, int(np.ceil(length / len(noise))))
    start = int(rng.integers(0, max(1, len(noise) - length)))
    return noise[start : start + length]


def mix(clean: np.ndarray, noise: np.ndarray, snr_db: float) -> np.ndarray:
    """지정한 SNR로 섞는다. 잡음 제거 A/B 측정과 같은 식이라 값을 나란히 볼 수 있다."""
    energy = float(np.sum(clean**2))
    noise_energy = float(np.sum(noise**2))
    if energy <= 0 or noise_energy <= 0:
        return clean
    scale = np.sqrt(energy / (noise_energy * 10 ** (snr_db / 10)))
    return np.clip(clean + noise * scale, -1.0, 1.0)


def micro(pairs: list[tuple[str, str]], unit: str) -> float:
    """코퍼스 단위 오류율 — 총 편집거리 ÷ 총 정답 길이."""
    distance = 0
    length = 0
    for reference, hypothesis in pairs:
        if unit == "char":
            a = list("".join(reference.split()))
            b = list("".join(hypothesis.split()))
        else:
            a = reference.split()
            b = hypothesis.split()
        distance += _edit_distance(a, b)
        length += len(a)
    return distance / length if length else 0.0


def select(root: Path, split: str, min_chars: int, limit: int, rng):
    """평가 대상을 고르고 무엇을 왜 뺐는지 함께 돌려준다."""
    rows = parse_trn(root / "scripts" / f"{split}.trn")
    kept, dropped_latin, dropped_short = [], 0, 0
    for relative, raw in rows:
        reference = normalize_for_scoring(clean_transcript(raw))
        if not reference:
            continue
        if has_latin(reference):
            dropped_latin += 1
            continue
        if min_chars and len("".join(reference.split())) < min_chars:
            dropped_short += 1
            continue
        kept.append((relative, reference))
    total = len(kept)
    if limit and limit < len(kept):
        index = rng.choice(len(kept), size=limit, replace=False)
        kept = [kept[i] for i in sorted(index)]
    return kept, {
        "전사": len(rows),
        "라틴 제외": dropped_latin,
        f"{min_chars}자 미만 제외": dropped_short,
        "대상": total,
        "추출": len(kept),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, required=True, help="KsponSpeech 루트")
    parser.add_argument("--noise-dir", type=Path, required=True, help="소음 wav 폴더")
    parser.add_argument("--split", default="eval_clean", choices=["eval_clean", "eval_other"])
    parser.add_argument("--limit", type=int, default=0, help="0이면 전량")
    parser.add_argument("--min-chars", type=int, default=5)
    parser.add_argument("--snr", type=float, nargs="*", default=[10, 5, 0])
    parser.add_argument("--out", type=Path, default=None, help="결과 JSONL 경로")
    parser.add_argument(
        "--no-prompt-compare",
        action="store_true",
        help="운영 프롬프트만 쓰고 비교를 생략한다 (시간 절반)",
    )
    args = parser.parse_args()

    root = args.root.expanduser()
    from sentinel_voice import config
    from sentinel_voice.audio import normalize
    from sentinel_voice.pipeline import load_models

    _, stt = load_models()

    def run(wav: np.ndarray, prompt: str | None) -> str:
        segments, _ = stt.transcribe(wav, initial_prompt=prompt, **config.STT_DECODE)
        return "".join(segment.text for segment in segments).strip()

    print(f"STT 설정  {config.summary()}")
    rng = np.random.default_rng(SEED)
    kept, counts = select(root, args.split, args.min_chars, args.limit, rng)
    print(f"{args.split}.trn  " + "  ".join(f"{k} {v}" for k, v in counts.items()))
    print(f"시드 {SEED}")

    noises = {p.stem: load_noise(p) for p in sorted(args.noise_dir.expanduser().glob("*.wav"))}
    if not noises:
        print(f"!! 소음 wav가 없다: {args.noise_dir}")
        return 2
    conditions = ["clean"] + [f"snr{int(s)}" for s in args.snr]
    prompts = {"운영": config.STT_PROMPT}
    if not args.no_prompt_compare:
        prompts["무프롬"] = None
    print(f"소음 {len(noises)}종  조건 {conditions}  프롬프트 {list(prompts)}")
    print(f"인식 {len(kept) * len(conditions) * len(prompts)}회")
    print()

    out = args.out or (root / f"bench_{args.split}_{len(kept)}.jsonl")
    out.parent.mkdir(parents=True, exist_ok=True)
    pairs = {(c, p): [] for c in conditions for p in prompts}
    names = list(noises)
    regurgitated = dict.fromkeys(prompts, 0)
    started = time.time()
    audio_seconds = 0.0

    with out.open("w", encoding="utf-8") as handle:
        for i, (relative, reference) in enumerate(kept, start=1):
            path = root / relative.replace(TRN_PREFIX, "eval/")
            if not path.is_file():
                print(f"  !! 없음 {path}")
                continue
            clean = read_pcm(path)
            audio_seconds += len(clean) / FS
            # 발화마다 소음을 돌려 쓴다. 한 종류로 몰면 그 소음의 성질만 재게 된다.
            noise_name = names[i % len(names)]
            noise = fit(noises[noise_name], len(clean), rng)

            for condition in conditions:
                audio = clean if condition == "clean" else mix(clean, noise, float(condition[3:]))
                wav = normalize(audio).astype(np.float32)
                for label, prompt in prompts.items():
                    hypothesis = normalize_for_scoring(run(wav, prompt))
                    pairs[(condition, label)].append((reference, hypothesis))
                    # 프롬프트 문장이 인식 결과로 되돌아 나오는지 센다(환각의 한 형태).
                    if "도와주세요" in hypothesis and "도와주세요" not in reference:
                        regurgitated[label] += 1
                    handle.write(
                        json.dumps(
                            {
                                "file": Path(relative).name,
                                "condition": condition,
                                "prompt": label,
                                "noise": None if condition == "clean" else noise_name,
                                "reference": reference,
                                "hypothesis": hypothesis,
                                "cer": round(cer(reference, hypothesis), 4),
                                "wer": round(wer(reference, hypothesis), 4),
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
            if i % 100 == 0:
                print(f"  {i}/{len(kept)}  경과 {(time.time()-started)/60:.1f}분")

    print()
    print("코퍼스 단위 (총 편집거리 ÷ 총 정답 길이) — 주 지표")
    header = f"{'조건':8}" + "".join(f"{'CER ' + p:>10}" for p in prompts)
    header += "".join(f"{'WER ' + p:>10}" for p in prompts)
    print(header)
    for condition in conditions:
        line = f"{condition:8}"
        for unit in ("char", "word"):
            for label in prompts:
                line += f"{micro(pairs[(condition, label)], unit):10.3f}"
        print(line)
    print()
    print("발화별 CER 중간값 — 초단문에 민감해 참고용")
    for condition in conditions:
        parts = [
            f"{label} {statistics.median([cer(r, h) for r, h in pairs[(condition, label)]]):.3f}"
            for label in prompts
        ]
        print(f"{condition:8} " + "  ".join(parts))
    print()
    print(f"프롬프트 문장이 결과로 되돌아 나온 횟수: {regurgitated}")
    print(f"오디오 {audio_seconds/60:.1f}분  ·  처리 {(time.time()-started)/60:.1f}분")
    print(f"기록 {out}")
    print()
    print("KsponSpeech는 일상 자유대화다. 이 값은 한국어 구어체 받아쓰기 성능이며")
    print("재난 현장(마이크·잔향·약한 발화·도메인 어휘) 성능이 아니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
