"""이벤트 영상의 오디오 트랙을 추출해 잡음을 제거하고 관제 청취용 자산을 만든다.

    python enhance_media.py event.mp4              # event-denoised.m4a
    python enhance_media.py event.mp4 -o out.m4a
    python enhance_media.py event.mp4 --wav        # 검청용 WAV도 함께

입력은 오디오 트랙이 있는 어떤 컨테이너든 된다(MP4·MKV·WAV …). 젯슨 이벤트
영상 규격은 H.264 + AAC 48kHz mono다(sentinel_streaming media.yaml). 48kHz는
DeepFilterNet의 고유 샘플레이트라 그 규격에서는 재샘플이 일어나지 않는다.

출력(m4a)은 관제 블랙박스 토글의 두 번째 소스다. **원본을 대체하지 않는다** —
잡음 제거는 사람 귀 전용이고 STT에는 해롭다는 실측이 있다
(ai/stt/docs/measurements/잡음제거-실측.md §3).
"""

from __future__ import annotations

import argparse
import sys
import time
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

DF_SAMPLE_RATE = 48000

# enhance()는 입력 전체를 한 번에 처리해 RAM이 길이에 비례한다(120초 ≈ 1.3GB 실측).
# 조각 처리로 상한을 고정한다. 경계 불연속은 겹침 구간 크로스페이드로 지운다.
CHUNK_SECONDS = 30.0
OVERLAP_SECONDS = 1.0

# 최대 감쇠 상한(dB). None이면 모델이 원하는 만큼 깎는다.
#
# 실제 마이크로 목소리와 소음을 동시에 녹음한 파일에서는 무제한이 과하다.
# 아이폰 녹음(음성대역 소음 52%, 구간 레벨차 4.9dB) 실측:
#
#   상한      소음 억제   발화 1k~3.4kHz 잔존   STT
#   무제한    -19.1dB     5.9%                 붕괴(프롬프트 반출)
#   15dB      -12.6dB     10.6%                붕괴
#   10dB       -9.0dB     17.8%                부분 복구
#
# 합성 혼합 시험에서는 이 문제가 드러나지 않았다 — 모델의 훈련 방식(가산 혼합)과
# 같은 조건이라 유리했기 때문이다. 근거: docs/measurements/잡음제거-실측.md §9
#
# 값은 관제 청취 판정으로 확정한다. 사람 귀가 소비자이므로 지표가 아니라 귀가 기준이다.
ATTEN_LIMIT_DB: float | None = None


# 디지털 무음 판정 임계값. 살아 있는 마이크는 조용해도 정확히 0을 내지 않으므로,
# 이 값 이하는 "조용했다"가 아니라 "마이크가 아닌 것을 녹음했다"는 뜻이다.
# ai/stt의 `config.SILENT_INPUT_PEAK`와 같은 근거다(S15P11A301-257).
SILENT_PEAK = 1e-6


class NoAudioTrack(ValueError):
    """입력에 오디오 트랙이 없다. 오디오 없는 이벤트 영상도 유효한 입력이므로
    호출자가 '처리 불가'와 '처리 실패'를 구분할 수 있게 따로 둔다."""


class SilentAudioTrack(ValueError):
    """오디오 트랙은 있으나 전 구간이 디지털 무음이다.

    `NoAudioTrack`과 갈라 둔 이유가 있다. 트랙이 없는 것은 정상 경로일 수 있지만
    (젯슨이 마이크를 못 열면 비디오만 기록한다), **트랙이 있는데 내용이 0인 것은
    캡처 경로 사망이다.** 마이크가 아닌 것을 녹음하고 있다는 뜻이다.

    2026-08-04 리허설 영상 295초가 이 상태였다. 그때 이 구분이 없어서 무음을
    잡음 제거해 무음을 업로드했고, 스캔은 그 발견을 영구히 완료로 표시했다.
    아무도 마이크 사망을 몰랐다.
    """


def extract_audio(path: str | Path, rate: int = DF_SAMPLE_RATE) -> np.ndarray:
    """컨테이너에서 오디오 트랙만 디코딩해 mono float32 [-1, 1]로 돌려준다."""
    import av

    chunks: list[np.ndarray] = []
    with av.open(str(path)) as container:
        if not container.streams.audio:
            raise NoAudioTrack(f"오디오 트랙 없음: {path}")
        resampler = av.AudioResampler(format="s16", layout="mono", rate=rate)
        for frame in container.decode(audio=0):
            for out in resampler.resample(frame):
                chunks.append(out.to_ndarray().reshape(-1))
        for out in resampler.resample(None):  # 리샘플러 내부 잔량 회수
            chunks.append(out.to_ndarray().reshape(-1))
    if not chunks:
        raise NoAudioTrack(f"오디오 트랙이 비어 있음: {path}")
    return (np.concatenate(chunks).astype(np.float32) / 32768.0).clip(-1.0, 1.0)


_model_state: tuple | None = None


def _load_model() -> tuple:
    """(model, state, enhance 함수)를 돌려준다.

    `df`는 import 자체가 구 torchaudio API를 요구하므로, shim을 적용한 뒤에만
    import할 수 있다. 호출자가 `from df.enhance import ...`를 직접 하면 순서
    실수가 나기 쉬워 enhance 함수까지 여기서 넘긴다.
    """
    global _model_state
    if _model_state is None:
        from torchaudio_compat import ensure_backend_module

        ensure_backend_module()
        from df.enhance import enhance, init_df

        model, state, _ = init_df(log_level="ERROR")
        _model_state = (model, state, enhance)
    return _model_state


def denoise(
    wav: np.ndarray,
    *,
    chunk_seconds: float = CHUNK_SECONDS,
    overlap_seconds: float = OVERLAP_SECONDS,
    atten_lim_db: float | None = ATTEN_LIMIT_DB,
) -> np.ndarray:
    """48kHz mono float32를 조각 단위로 잡음 제거한다. 길이를 보존한다.

    `atten_lim_db`로 최대 감쇠를 제한한다 (None이면 무제한). 과잉 억제가
    의심되면 낮춘다 — 상수 정의의 실측표 참고.
    """
    import torch

    model, state, enhance = _load_model()
    if state.sr() != DF_SAMPLE_RATE:  # 모델 규격이 바뀌면 조용히 틀리는 대신 멈춘다
        raise RuntimeError(f"모델 샘플레이트 {state.sr()} != {DF_SAMPLE_RATE}")

    total = len(wav)
    chunk = int(chunk_seconds * DF_SAMPLE_RATE)
    overlap = int(overlap_seconds * DF_SAMPLE_RATE)
    if total <= chunk:
        tensor = torch.from_numpy(np.ascontiguousarray(wav, dtype=np.float32))
        out = (
            enhance(model, state, tensor.unsqueeze(0), atten_lim_db=atten_lim_db)
            .squeeze(0)
            .detach()
            .numpy()
        )
        return _match_length(out, total)

    fade_in = np.linspace(0.0, 1.0, overlap, dtype=np.float32)
    result = np.zeros(total, dtype=np.float32)
    start = 0
    while start < total:
        end = min(start + chunk, total)
        piece = np.ascontiguousarray(wav[start:end], dtype=np.float32)
        tensor = torch.from_numpy(piece).unsqueeze(0)
        cleaned = (
            enhance(model, state, tensor, atten_lim_db=atten_lim_db)
            .squeeze(0)
            .detach()
            .numpy()
        )
        cleaned = _match_length(cleaned, end - start)

        if start == 0:
            result[start:end] = cleaned
        else:
            n = min(overlap, end - start)
            result[start : start + n] = (
                result[start : start + n] * (1.0 - fade_in[:n]) + cleaned[:n] * fade_in[:n]
            )
            result[start + n : end] = cleaned[n:]
        if end == total:
            break
        start = end - overlap
    return result


def _match_length(x: np.ndarray, n: int) -> np.ndarray:
    """모델 출력 길이를 입력과 표본 단위로 맞춘다. 토글 동기의 전제다."""
    if len(x) >= n:
        return x[:n]
    return np.pad(x, (0, n - len(x)))


def write_m4a(path: str | Path, wav: np.ndarray, *, rate: int = DF_SAMPLE_RATE) -> None:
    """AAC 64kbps로 인코딩한다. 이벤트 영상 오디오와 같은 코덱·비트레이트다.

    프레임 단위로 pts를 박아 넣는다. 전체를 한 프레임으로 넘기면 샘플 테이블에
    타임스탬프가 서지 않아 브라우저에서 `seekable`이 [0, 0]이 된다 — 재생은
    되지만 탐색이 안 되고, 그러면 관제 토글의 위치 동기가 불가능하다(실측).
    """
    from fractions import Fraction

    import av

    samples_per_frame = 1024  # AAC 고정 프레임 크기
    pcm = np.clip(wav * 32767.0, -32768, 32767).astype("<i2")
    with av.open(str(path), "w") as container:
        stream = container.add_stream("aac", rate=rate)
        stream.bit_rate = 64_000
        stream.layout = "mono"
        stream.time_base = Fraction(1, rate)
        for start in range(0, len(pcm), samples_per_frame):
            chunk = pcm[start : start + samples_per_frame].reshape(1, -1)
            frame = av.AudioFrame.from_ndarray(chunk, format="s16", layout="mono")
            frame.sample_rate = rate
            frame.pts = start
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode(None):
            container.mux(packet)


def write_wav(path: str | Path, wav: np.ndarray, *, rate: int = DF_SAMPLE_RATE) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(np.clip(wav * 32767.0, -32768, 32767).astype("<i2").tobytes())


@dataclass(frozen=True)
class EnhanceResult:
    """처리 결과. `seconds`는 완료 API의 `durationSeconds`에 넣는다."""

    path: Path
    seconds: float
    elapsed_seconds: float


def enhance_media(
    source: str | Path,
    output: str | Path | None = None,
    *,
    also_wav: bool = False,
    atten_lim_db: float | None = ATTEN_LIMIT_DB,
    quiet: bool = False,
) -> EnhanceResult:
    """영상에서 오디오를 뽑아 잡음을 제거하고 m4a로 저장한다."""
    source = Path(source)
    target = Path(output) if output else source.with_name(f"{source.stem}-denoised.m4a")

    wav = extract_audio(source)
    seconds = len(wav) / DF_SAMPLE_RATE
    # 잡음 제거보다 먼저 본다. 무음을 제거해도 무음이고, 그 결과를 올리면 마이크
    # 사망이 조용한 성공으로 덮인다. CPU도 아낀다.
    peak = float(np.max(np.abs(wav))) if len(wav) else 0.0
    if peak <= SILENT_PEAK:
        raise SilentAudioTrack(
            f"오디오 트랙 전체가 디지털 무음(peak {peak:.8f}, {seconds:.1f}초): {source}"
        )
    t0 = time.perf_counter()
    cleaned = denoise(wav, atten_lim_db=atten_lim_db)
    elapsed = time.perf_counter() - t0

    write_m4a(target, cleaned)
    if also_wav:
        write_wav(target.with_suffix(".wav"), cleaned)
    if not quiet:
        ratio = elapsed / seconds if seconds > 0 else 0.0
        print(f"{source.name}: {seconds:.1f}초 오디오, 제거 {elapsed:.1f}초 (x{ratio:.2f}), → {target}")
    return EnhanceResult(target, seconds, elapsed)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("source", help="오디오 트랙이 있는 영상/음성 파일")
    parser.add_argument("-o", "--output", default=None, help="출력 m4a 경로")
    parser.add_argument("--wav", action="store_true", help="검청용 WAV도 함께 저장")
    parser.add_argument(
        "--atten-lim-db",
        type=float,
        default=ATTEN_LIMIT_DB,
        help="최대 감쇠 상한(dB). 생략하면 무제한. 과잉 억제 시 10~15를 시도한다",
    )
    args = parser.parse_args()
    try:
        enhance_media(
            args.source,
            args.output,
            also_wav=args.wav,
            atten_lim_db=args.atten_lim_db,
        )
    except NoAudioTrack as error:
        print(f"입력에 오디오가 없습니다: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
