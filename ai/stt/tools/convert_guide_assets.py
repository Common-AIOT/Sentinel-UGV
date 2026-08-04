"""MiniMax 원본 WAV를 Jetson 안내 음성 규격으로 일괄 변환한다.

입력 예:
    mini_intro.wav
    mini_ask_count.wav

출력 예:
    assets/guide_intro.wav
    assets/guide_ask_count.wav

변환은 **PyAV**(`av`)로 한다. PyAV가 ffmpeg의 libavfilter를 품고 있어 `loudnorm`
필터를 그대로 쓸 수 있고, ffmpeg 실행 파일을 따로 설치하지 않는다.

> ffmpeg CLI 폴백을 두지 않는 이유 — 시스템 ffmpeg와 PyAV의 libav는 버전이 다를
> 수 있고, `loudnorm` 결과가 버전에 따라 달라진다. 실행하는 사람에 따라 자산 음량이
> 달라지면 §6-2가 규격을 고정하는 의미가 없다. 경로를 하나로 둔다.

MiniMax 출력은 헤더의 프레임 수가 실제와 다른 경우가 있다(스트리밍 인코딩).
디코딩한 샘플로 다시 쓰므로 그 오류는 여기서 교정된다.

변환은 두 단계다 — **시간 확장(`atempo`) 다음 라우드니스 정규화(`loudnorm`)**.
확장 배율의 근거는 `TEMPO` 주석과 `docs/README.md` §6-2에 있다.
"""

from __future__ import annotations

import argparse
import re
import wave
from fractions import Fraction
from pathlib import Path

import numpy as np

from sentinel_voice import config
from sentinel_voice.guide_audio import GUIDE_ASSETS, validate_wav

# EBU R128 라우드니스 정규화. 목표 −20 LUFS · 트루피크 −2dBTP.
# validate_wav의 RMS −32~−12dBFS, peak ≤ −1dBFS를 만족시키기 위한 값이다.
LOUDNORM = "loudnorm=I=-20:TP=-2:LRA=7"

# 시간 확장 배율 (S15P11A301-260). 1.0이면 원본 속도 그대로다.
#
# **원본이 너무 빨랐다.** MiniMax Speed 1.1로 생성한 v2 자산의 발화 속도가
# 6.9~8.2 음절/초였다. 일상 대화가 5~7 음절/초이므로 안내 방송인데 대화보다 빨랐다.
# 0.8을 적용하면 최고 8.2 → 6.7 음절/초가 된다(2026-08-04 청취 확정).
#
# **왜 MiniMax에서 다시 뽑지 않는가** — Speed를 내려 재생성하면 톤이 함께 달라진다.
# 현재 톤은 유지하기로 했으므로 생성은 그대로 두고 변환에서 시간만 늘린다.
#
# **왜 atempo인가** — 음높이를 바꾸지 않고 시간만 늘린다(구간 중첩 가산). 리샘플로
# 늘리면 음높이가 함께 내려가 톤이 변한다. 그것이 이 작업의 목적과 정반대다.
#
# 이 값은 **원본이 MiniMax Speed 1.1일 때** 최종 속도를 맞추는 값이다. 원본을 다른
# Speed로 다시 뽑으면 이 값도 다시 골라야 한다. 근거는 docs/README.md §6-2.
TEMPO = 0.8

# 발화 속도 표시용. 한글 음절 블록만 센다 — 문장부호·공백은 발화 시간이 아니다.
_HANGUL = re.compile(r"[가-힣]")


def source_filename(output_filename: str) -> str:
    """guide_xxx.wav에 대응하는 MiniMax 원본명 mini_xxx.wav를 반환한다."""
    if not output_filename.startswith("guide_"):
        raise ValueError(f"안내 파일명 규칙 위반: {output_filename}")
    return f"mini_{output_filename.removeprefix('guide_')}"


def convert_one(
    source: Path, output: Path, *, force: bool, tempo: float = TEMPO
) -> None:
    """원본을 16kHz mono PCM16으로 늘리고 라우드니스 정규화해 쓴다."""
    import av
    from av.filter import Graph

    if output.exists() and not force:
        raise FileExistsError(
            f"{output} 파일이 이미 있음. 교체하려면 --force를 사용하세요."
        )
    if not 0.5 <= tempo <= 2.0:
        # atempo 자체는 0.5~100을 받지만, 안내 음성에서 그 범위를 벗어난 값은
        # 실수다. 규격을 벗어난 자산이 조용히 만들어지는 것을 막는다.
        raise ValueError(f"시간 확장 배율 범위(0.5~2.0) 이탈: {tempo}")

    with av.open(str(source)) as container:
        stream = container.streams.audio[0]

        graph = Graph()
        nodes = [graph.add_abuffer(template=stream)]
        if tempo != 1.0:
            # **순서가 중요하다.** 늘린 뒤에 라우드니스를 잰다. 반대로 하면 정규화
            # 이후 시간축이 바뀌어 측정 근거와 결과가 어긋난다.
            nodes.append(graph.add("atempo", f"tempo={tempo}"))
        nodes += [
            graph.add("loudnorm", LOUDNORM.split("=", 1)[1]),
            graph.add(
                "aformat",
                f"sample_fmts=s16:sample_rates={config.FS}:channel_layouts=mono",
            ),
            graph.add("abuffersink"),
        ]
        for upstream, downstream in zip(nodes, nodes[1:]):
            upstream.link_to(downstream)
        graph.configure()

        chunks: list[np.ndarray] = []

        def drain() -> None:
            while True:
                try:
                    out = graph.pull()
                except (av.BlockingIOError, av.EOFError):
                    return
                chunks.append(out.to_ndarray().reshape(-1))

        for frame in container.decode(audio=0):
            # loudnorm은 2-pass 필터지만 필터그래프에서는 단일 패스로 동작한다.
            # 프레임 pts를 유지해야 필터가 시간축을 잃지 않는다.
            graph.push(frame)
            drain()
        graph.push(None)  # flush — 마지막 버퍼를 밀어낸다
        drain()

    if not chunks:
        raise ValueError("변환 결과가 비어 있음")

    samples = np.concatenate(chunks).astype("<i2")
    output.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(output), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(config.FS)
        wav.writeframes(samples.tobytes())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source-dir",
        type=Path,
        default=config.STT_ROOT,
        help="mini_*.wav가 있는 폴더(기본: ai/stt)",
    )
    parser.add_argument(
        "--assets-dir",
        type=Path,
        default=config.STT_ROOT / "assets",
        help="변환된 guide_*.wav 출력 폴더",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="기존 assets/guide_*.wav를 새 파일로 교체",
    )
    parser.add_argument(
        "--tempo",
        type=float,
        default=TEMPO,
        help=(
            f"시간 확장 배율(기본 {TEMPO}). 1.0은 원본 속도. 음높이는 바뀌지 않는다."
            " 기본값이 커밋된 자산을 재현하는 값이다"
        ),
    )
    args = parser.parse_args()

    try:
        import av  # noqa: F401
    except ImportError:
        print("[FAIL] PyAV가 없습니다. pip install av")
        return 1

    failures = 0
    for code, asset in GUIDE_ASSETS.items():
        source = args.source_dir / source_filename(asset.filename)
        output = args.assets_dir / asset.filename

        if not source.is_file():
            print(f"[FAIL] {code.value:<29} 원본 없음: {source}")
            failures += 1
            continue

        try:
            convert_one(source, output, force=args.force, tempo=args.tempo)
            inspection = validate_wav(output)
            # 발화 속도를 함께 찍는다. 이 값이 260의 판정 대상이고, 규격 검사
            # (길이·레벨)만으로는 "너무 빠르다"가 드러나지 않는다.
            syllables = len(_HANGUL.findall(asset.text))
            rate = syllables / inspection.duration_seconds
            print(
                f"[OK]   {code.value:<29} {source.name} -> "
                f"{output.name} "
                f"({inspection.duration_seconds:.2f}s, "
                f"{rate:.1f}음절/초, "
                f"RMS={inspection.rms_dbfs:.1f}dBFS, "
                f"peak={inspection.peak_dbfs:.1f}dBFS)"
            )
        except (FileExistsError, OSError, ValueError) as exc:
            print(f"[FAIL] {code.value:<29} {type(exc).__name__}: {exc}")
            failures += 1

    if failures:
        print(f"\nFAIL {failures}개 - 원본명과 변환 오류를 확인하세요.")
        return 1

    print(
        f"\nOK - 안내 음성 {len(GUIDE_ASSETS)}개 변환·검증 완료 "
        f"(시간 확장 {args.tempo})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
