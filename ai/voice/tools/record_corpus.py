"""정답을 아는 발화 코퍼스를 모은다 (S15P11A301-120 · S15P11A301-202).

    python -m tools.record_corpus --list                     문장 목록만 본다
    python -m tools.record_corpus --devices                   입력 장치 목록
    python -m tools.record_corpus                             전체 녹음 (조용한 조건)
    python -m tools.record_corpus --condition noisy           소음을 틀어놓고 녹음
    python -m tools.record_corpus --intensity weak            약하게 말하는 조건만
    python -m tools.record_corpus --only 3 7 --device 2        일부만 다시

## 왜 필요한가

두 작업이 같은 이유로 막혀 있다.

  120 (STT WER·슬롯 정확도)
      178이 저장한 9세션 38턴 중 **정답을 세울 수 있는 발화가 6개뿐**이다. 나머지는
      무음이거나 의도적으로 뒤죽박죽 말한 시험이라 기준이 없다. "실제 육성이
      코퍼스가 되어 제약이 풀렸다"는 앞선 서술은 낙관적이었다.

  202 (잡음 제거 실기 재측정)
      지금까지의 측정은 깨끗한 음성에 소음을 **숫자로 더한** 합성이다. 그것은
      DeepFilterNet의 훈련 방식(가산 혼합)과 같아 모델에 유리하다. 실제 마이크로
      동시에 녹음한 것은 1건뿐이다. 근거: `docs/08-AI-음성.md` 33.9

**한 번의 녹음으로 둘 다 풀린다.** 문장을 띄우고 읽게 하면 정답이 확정되고,
스피커로 소음을 틀어놓고 같은 절차를 반복하면 실기 동시 녹음이 된다.

## 48kHz로 녹음한다

파이프라인 표준은 16kHz(`config.FS`)인데 여기서는 48kHz로 받는다. 이유:

  - 잡음 제거기(DeepFilterNet)의 고유 레이트가 48kHz다. 16kHz로 받아 올리면
    모델이 학습한 대역 조건과 어긋난다.
  - 이벤트 영상 오디오도 48kHz다(`sentinel_streaming/config/media.yaml`).
  - STT 측정은 48k에서 16k로 내려서 하면 된다. 대부분의 USB 마이크가 48k 원본이라
    16kHz 직접 요청도 드라이버가 내부에서 내리는 것이라 손실 관계는 같다.

한 번 모으면 STT 측정과 잡음 제거 측정에 **같은 파일**을 쓸 수 있다.

## 개인정보

`ai/voice/.gitignore`가 `sessions/`를 막고 있는 것과 같은 취급이다. **어떤 경우에도
커밋하지 않는다.** 기본 저장 위치를 저장소 밖(`~/audio-test/corpus`)으로 둔 이유다.
시험을 수행한 사람이 삭제 책임을 진다(문서 §11-6과 같은 규칙).
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import sounddevice as sd
import soundfile as sf

SAMPLE_RATE = 48_000
DEFAULT_ROOT = Path("~/audio-test/corpus").expanduser()

# 운영 임계값과 같은 기준으로 판정한다. 여기서 통과한 녹음은 파이프라인에서도
# 무음으로 버려지지 않는다.
SILENCE_RMS = 0.001
CLIPPING_PEAK = 0.99


@dataclass(frozen=True)
class Line:
    """읽을 문장 하나와 그에 대응하는 정답.

    `field`·`value`는 33-6 보고의 기대값이다. WER만으로는 "낱말은 틀렸지만 값은
    맞았다"를 구분할 수 없어 120이 슬롯 정확도를 따로 요구한다.
    """

    question: str
    text: str
    field: str | None = None
    value: Any = None
    note: str = ""


# 질문 4개(INTRO→URGENT→MOBILITY→COUNT)에 대한 실제 응답들. 값이 갈리는 경계와
# 알려진 실패 사례를 함께 넣었다.
LINES: list[Line] = [
    Line("INTRO", "네, 들립니다", "anyResponseDetected", True),
    Line("INTRO", "여기 사람 있어요", "anyResponseDetected", True),
    Line("INTRO", "도와주세요", "anyResponseDetected", True),

    Line("URGENT", "다친 곳은 없습니다", "urgentConditionReported", "NO"),
    Line("URGENT", "다리를 다쳤어요", "urgentConditionReported", "YES"),
    Line("URGENT", "피가 계속 나요", "urgentConditionReported", "YES"),
    Line("URGENT", "숨쉬기가 힘들어요", "urgentConditionReported", "YES",
         "환경 위험(연기)과 부상이 섞인 경계 사례 — 147 참고"),

    Line("MOBILITY", "움직일 수 있어요", "mobilityStatus", "YES",
         "알려진 실패 사례: 약하게 말하면 '이럴 수 있어요'로 인식된다 (§11-5)"),
    Line("MOBILITY", "혼자 걸을 수 있습니다", "mobilityStatus", "YES"),
    Line("MOBILITY", "다리가 눌려서 못 움직여요", "mobilityStatus", "NO"),
    Line("MOBILITY", "일어나려니까 너무 아파요", "mobilityStatus", "NO"),

    Line("COUNT", "저 혼자예요", "reportedResponsiveCount", 1),
    Line("COUNT", "저 포함해서 세 명이요", "reportedResponsiveCount", 3),
    Line("COUNT", "두 명 더 있어요", "reportedResponsiveCount", 1,
         "추가 두 명의 응답 여부는 미확인 — 현재 발화자 한 명만 응답 인원"),
    Line("COUNT", "옆에 한 명 있는데 대답을 안 해요", "reportedResponsiveCount", 1,
         "추가 제보는 additionalPersonReports.responseStatus=UNRESPONSIVE로 별도 보존"),

    Line("OTHER", "가스 냄새가 나요", "hazardReported", ["GAS"],
         "환경 위험. 현행은 urgentConditionReported에 뭉쳐 있다 (147)"),
]

INTENSITY_GUIDE = {
    "normal": "평소 목소리로 또박또박",
    "weak": "**작고 힘없는 목소리로** (중상자 상정 — 이 조건이 현장 기대치다)",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def db(value: float) -> float:
    return 20 * float(np.log10(max(value, 1e-9)))


def show_devices() -> None:
    print(f"{'idx':>4} {'in':>3} {'기본Hz':>8}  이름")
    print("-" * 72)
    default_input = sd.default.device[0]
    for index, device in enumerate(sd.query_devices()):
        channels = int(device["max_input_channels"])
        if channels < 1:
            continue
        mark = " ←기본" if index == default_input else ""
        print(
            f"{index:>4} {channels:>3} {float(device['default_samplerate']):>8.0f}  "
            f"{device['name']}{mark}"
        )
    print()
    print("소음 조건으로 녹음할 때는 소음을 스피커로 틀어 공기 중에 울려야 한다.")
    print("헤드폰으로만 나가면 마이크에 들어오지 않는다.")


def record(seconds: float, device: int | None) -> np.ndarray:
    """48kHz mono로 받는다. 실패하면 장치 기본 레이트로 한 번 더 시도한다."""
    frames = int(seconds * SAMPLE_RATE)
    try:
        data = sd.rec(frames, samplerate=SAMPLE_RATE, channels=1, dtype="float32", device=device)
        sd.wait()
        return np.asarray(data, dtype=np.float64).reshape(-1)
    except Exception as error:
        info = sd.query_devices(device if device is not None else sd.default.device[0])
        rate = int(info["default_samplerate"])
        print(f"    48kHz 실패({type(error).__name__}) — 장치 기본 {rate}Hz로 재시도")
        data = sd.rec(int(seconds * rate), samplerate=rate, channels=1, dtype="float32", device=device)
        sd.wait()
        from scipy.signal import resample_poly

        return resample_poly(np.asarray(data, dtype=np.float64).reshape(-1), SAMPLE_RATE, rate)


def judge(wav: np.ndarray) -> tuple[str, bool]:
    """(사람이 읽는 판정, 다시 녹음 권고 여부)."""
    rms = float(np.sqrt(np.mean(wav**2)))
    peak = float(np.max(np.abs(wav)))
    label = f"RMS {db(rms):6.1f} dBFS · peak {db(peak):6.1f} dBFS"
    if rms < SILENCE_RMS:
        return f"{label}  ⚠ 너무 작다 (운영이 무음으로 버리는 수준)", True
    if peak > CLIPPING_PEAK:
        return f"{label}  ⚠ 클리핑", True
    return f"{label}  좋다", False


def ask(prompt: str, choices: str) -> str:
    """사용자 입력 한 글자를 받는다. 그냥 Enter는 첫 선택지다."""
    while True:
        answer = input(f"    {prompt} [{choices}] ").strip().lower()
        if not answer:
            return choices[0]
        if answer[0] in choices:
            return answer[0]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=str(DEFAULT_ROOT), help="저장 위치 (저장소 밖)")
    parser.add_argument("--device", type=int, default=None, help="입력 장치 번호")
    parser.add_argument("--seconds", type=float, default=6.0, help="문장당 녹음 길이")
    parser.add_argument(
        "--condition",
        default="quiet",
        choices=["quiet", "noisy"],
        help="quiet=조용한 방 · noisy=소음을 스피커로 틀어놓음",
    )
    parser.add_argument(
        "--intensity", default="normal", choices=["normal", "weak"], help="발성 세기"
    )
    parser.add_argument("--noise", default="", help="noisy일 때 어떤 소음인지 메모")
    parser.add_argument("--only", type=int, nargs="*", default=None, help="문장 번호만")
    parser.add_argument("--list", action="store_true", help="문장 목록만 출력")
    parser.add_argument("--devices", action="store_true", help="입력 장치 목록만 출력")
    args = parser.parse_args()

    if args.devices:
        show_devices()
        return 0

    if args.list:
        print(f"{'#':>3} {'질문':9} {'정답 필드':26} 문장")
        print("-" * 96)
        for i, line in enumerate(LINES, 1):
            slot = f"{line.field}={line.value}" if line.field else "—"
            print(f"{i:>3} {line.question:9} {slot:26} {line.text}")
            if line.note:
                print(f"{'':>40} ↳ {line.note}")
        print()
        print(f"총 {len(LINES)}문장. 세기 2종(normal·weak) × 조건 2종(quiet·noisy)까지")
        print("모으면 문장당 4개다. 세기·조건은 실행할 때마다 하나씩 지정한다.")
        return 0

    if args.condition == "noisy" and not args.noise:
        print("!! --condition noisy 일 때는 --noise 로 어떤 소음인지 남겨야 한다")
        print("   예: --noise realmotor  또는  --noise '유튜브 화재음, 폰 스피커 30cm'")
        return 1

    targets = list(enumerate(LINES, 1))
    if args.only:
        wanted = set(args.only)
        targets = [(i, line) for i, line in targets if i in wanted]
        if not targets:
            print("!! --only 에 해당하는 문장이 없다")
            return 1

    root = Path(args.root).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    manifest = root / "manifest.jsonl"

    print("=" * 84)
    print(f"저장 위치   {root}")
    print(f"조건        {args.condition}" + (f" ({args.noise})" if args.noise else ""))
    print(f"발성        {args.intensity} — {INTENSITY_GUIDE[args.intensity]}")
    print(f"녹음 길이   문장당 {args.seconds:.0f}초 · {SAMPLE_RATE}Hz mono")
    print(f"문장        {len(targets)}개")
    print("=" * 84)
    if args.condition == "noisy":
        print("소음이 공기 중에 울리고 있는지 확인한다. 헤드폰으로만 나가면 안 들어온다.")
    print()

    saved = 0
    for order, (number, line) in enumerate(targets, 1):
        while True:
            print(f"[{order}/{len(targets)}] 문장 {number} · {line.question}")
            print(f"    ┏━ {line.text}")
            print(f"    ┗━ {INTENSITY_GUIDE[args.intensity]}")
            if line.note:
                print(f"       ({line.note})")
            choice = input("    Enter=녹음 · s=건너뛰기 · q=종료: ").strip().lower()
            if choice.startswith("q"):
                print(f"\n중단. {saved}개 저장됨.")
                return 0
            if choice.startswith("s"):
                print("    건너뜀\n")
                break

            print("    ● 녹음 중...", end="", flush=True)
            wav = record(args.seconds, args.device)
            print(" 끝")

            verdict, retake = judge(wav)
            print(f"    {verdict}")

            default = "rsq" if retake else "srq"
            prompt = "다시 녹음 권장 — r=다시 · s=그래도 저장 · q=종료" if retake else \
                     "s=저장 · r=다시 · q=종료"
            action = ask(prompt, default)
            if action == "q":
                print(f"\n중단. {saved}개 저장됨.")
                return 0
            if action == "r":
                print()
                continue

            stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            name = f"{number:02d}_{line.question}_{args.intensity}_{args.condition}_{stamp}.wav"
            sf.write(str(root / name), wav, SAMPLE_RATE, subtype="PCM_16")

            record_row: dict[str, Any] = {
                "file": name,
                "at": utc_now(),
                "lineNumber": number,
                "question": line.question,
                "text": line.text,
                "expectedField": line.field,
                "expectedValue": line.value,
                "intensity": args.intensity,
                "condition": args.condition,
                "noise": args.noise or None,
                "sampleRate": SAMPLE_RATE,
                "seconds": round(len(wav) / SAMPLE_RATE, 3),
                "rms": round(float(np.sqrt(np.mean(wav**2))), 6),
                "peak": round(float(np.max(np.abs(wav))), 6),
                "device": args.device,
            }
            with manifest.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record_row, ensure_ascii=False) + "\n")

            saved += 1
            print(f"    저장 {name}\n")
            break

    print("=" * 84)
    print(f"{saved}개 저장. 목록: {manifest}")
    print()
    print("이 코퍼스로 할 수 있는 것")
    print("  120  text를 정답으로 WER·CER 측정 · expectedField/Value로 슬롯 정확도")
    print("  202  condition=noisy 녹음이 실기 동시 녹음이다 — 합성 혼합과 대조")
    print()
    print("⚠️ 커밋하지 않는다. 측정이 끝나면 삭제한다 (docs/08-AI-음성.md 33.5).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
