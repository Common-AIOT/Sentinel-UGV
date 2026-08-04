"""enhance_media의 추출·조각처리·인코딩 검증.

DeepFilterNet 모델이 필요한 테스트는 importorskip으로 분리했다 — 모델 없는
환경(CI·젯슨)에서도 추출·인코딩 경로는 검증된다.

시험 입력은 픽스처가 즉석에서 만든다. 이벤트 영상 규격(AAC 48kHz mono)과 같은
오디오 트랙을 가진 MP4를 PyAV로 합성하므로 저장소에 미디어 파일을 두지 않는다.
"""

from __future__ import annotations

import sys
import wave
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from enhance_media import (  # noqa: E402
    DF_SAMPLE_RATE,
    NoAudioTrack,
    SilentAudioTrack,
    _match_length,
    enhance_media,
    extract_audio,
    write_m4a,
    write_wav,
)

av = pytest.importorskip("av")


def tone(seconds: float, hz: float = 440.0, rate: int = DF_SAMPLE_RATE) -> np.ndarray:
    t = np.arange(int(seconds * rate)) / rate
    return (0.3 * np.sin(2 * np.pi * hz * t)).astype(np.float32)


def make_mp4(path: Path, audio: np.ndarray, *, rate: int = DF_SAMPLE_RATE) -> None:
    """이벤트 영상과 같은 오디오 규격(AAC 48kHz mono)의 MP4를 만든다.

    비디오 트랙은 mpeg4 8×8 회색 프레임이다 — 추출기가 비디오를 무시하는지
    확인하는 용도라 내용은 무관하다.
    """
    with av.open(str(path), "w") as container:
        video = container.add_stream("mpeg4", rate=10)
        video.width = video.height = 8
        video.pix_fmt = "yuv420p"
        astream = container.add_stream("aac", rate=rate)
        astream.bit_rate = 64_000
        astream.layout = "mono"

        pcm = np.clip(audio * 32767, -32768, 32767).astype("<i2").reshape(1, -1)
        frame = av.AudioFrame.from_ndarray(pcm, format="s16", layout="mono")
        frame.sample_rate = rate
        for packet in astream.encode(frame):
            container.mux(packet)
        for packet in astream.encode(None):
            container.mux(packet)

        gray = np.full((8, 8, 3), 128, dtype=np.uint8)
        for _ in range(int(len(audio) / rate * 10) or 1):
            vframe = av.VideoFrame.from_ndarray(gray, format="rgb24")
            for packet in video.encode(vframe):
                container.mux(packet)
        for packet in video.encode(None):
            container.mux(packet)


# ── 추출 ──────────────────────────────────────────────────────────
def test_extracts_audio_track_from_video(tmp_path):
    original = tone(2.0)
    source = tmp_path / "event.mp4"
    make_mp4(source, original)

    extracted = extract_audio(source)

    # AAC는 손실 압축이라 표본 일치가 아니라 길이(프레임 패딩 오차 이내)와
    # 내용(주파수 성분)으로 검증한다.
    assert abs(len(extracted) - len(original)) < DF_SAMPLE_RATE * 0.1
    spectrum = np.abs(np.fft.rfft(extracted))
    peak_hz = np.fft.rfftfreq(len(extracted), 1 / DF_SAMPLE_RATE)[int(np.argmax(spectrum))]
    assert abs(peak_hz - 440.0) < 5.0


def test_rejects_video_without_audio(tmp_path):
    source = tmp_path / "mute.mp4"
    with av.open(str(source), "w") as container:
        video = container.add_stream("mpeg4", rate=10)
        video.width = video.height = 8
        video.pix_fmt = "yuv420p"
        gray = np.full((8, 8, 3), 128, dtype=np.uint8)
        for _ in range(5):
            for packet in video.encode(av.VideoFrame.from_ndarray(gray, format="rgb24")):
                container.mux(packet)
        for packet in video.encode(None):
            container.mux(packet)

    with pytest.raises(NoAudioTrack):
        extract_audio(source)


def test_resamples_non_native_rate(tmp_path):
    """16kHz WAV(세션 녹음 규격)도 48kHz로 올려 받아들인다."""
    rate = 16000
    t = np.arange(rate) / rate
    source = tmp_path / "clip.wav"
    with wave.open(str(source), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(
            np.clip(0.3 * np.sin(2 * np.pi * 440 * t) * 32767, -32768, 32767)
            .astype("<i2")
            .tobytes()
        )

    extracted = extract_audio(source)
    assert abs(len(extracted) - DF_SAMPLE_RATE) < DF_SAMPLE_RATE * 0.05


# ── 인코딩 ────────────────────────────────────────────────────────
def test_m4a_roundtrip_preserves_duration_and_content(tmp_path):
    original = tone(3.0, hz=880.0)
    target = tmp_path / "out.m4a"
    write_m4a(target, original)

    decoded = extract_audio(target)
    assert abs(len(decoded) - len(original)) < DF_SAMPLE_RATE * 0.1
    spectrum = np.abs(np.fft.rfft(decoded))
    peak_hz = np.fft.rfftfreq(len(decoded), 1 / DF_SAMPLE_RATE)[int(np.argmax(spectrum))]
    assert abs(peak_hz - 880.0) < 5.0


def test_m4a_is_seekable(tmp_path):
    """m4a 중간으로 탐색이 돼야 한다.

    전체를 한 프레임으로 인코딩하면 pts가 서지 않아 브라우저 `seekable`이
    [0, 0]이 된다 — 관제 토글의 위치 동기가 불가능해진다(실측 회귀).
    """
    original = tone(10.0)
    target = tmp_path / "out.m4a"
    write_m4a(target, original)

    with av.open(str(target)) as container:
        stream = container.streams.audio[0]
        container.seek(int(5 / stream.time_base), stream=stream)
        frame = next(container.decode(audio=0))
        landed = float(frame.pts * stream.time_base)
    assert 3.0 < landed <= 5.5  # 5초 근처 키프레임에 내려앉아야 한다


def test_wav_roundtrip_is_lossless_within_quantization(tmp_path):
    original = tone(1.0)
    target = tmp_path / "out.wav"
    write_wav(target, original)
    with wave.open(str(target), "rb") as w:
        assert w.getframerate() == DF_SAMPLE_RATE
        decoded = (
            np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32)
            / 32768.0
        )
    assert len(decoded) == len(original)
    # 쓰기(×32767)와 읽기(÷32768)의 배율 차 + 반올림 = 최대 2 LSB 상당
    assert float(np.max(np.abs(decoded - original))) < 2.0 / 32768


def test_match_length_trims_and_pads():
    x = np.ones(10, dtype=np.float32)
    assert len(_match_length(x, 7)) == 7
    padded = _match_length(x, 13)
    assert len(padded) == 13
    assert float(padded[-1]) == 0.0


# ── 잡음 제거 (모델 필요) ─────────────────────────────────────────
def _require_df():
    """df는 import 자체가 구 torchaudio API를 요구한다. shim을 먼저 적용해야
    '설치돼 있는데 스킵'이 생기지 않는다."""
    from torchaudio_compat import ensure_backend_module

    ensure_backend_module()
    return pytest.importorskip("df")


def voiced_like(seconds: float) -> np.ndarray:
    """제거기가 음성으로 인정하는 합성 신호.

    단순 사인파는 제거기가 소음으로 보고 지워버린다(주기 기계음과 구별 불가 —
    올바른 동작이다). 음성으로 인정받으려면 흔들리는 피치 + 고조파 + 음절
    리듬이 필요하다. 이 조합의 잔차 개선은 실측으로 확인했다(0.0025→0.00006).
    """
    t = np.arange(int(seconds * DF_SAMPLE_RATE)) / DF_SAMPLE_RATE
    f0 = 120 + 30 * np.sin(2 * np.pi * 0.8 * t)
    phase = np.cumsum(2 * np.pi * f0 / DF_SAMPLE_RATE)
    voiced = np.zeros_like(t)
    for k, amp in ((1, 1.0), (2, 0.6), (3, 0.5), (4, 0.3), (5, 0.2)):
        voiced += amp * np.sin(k * phase)
    syllable = 0.5 + 0.5 * np.sin(2 * np.pi * 3.5 * t - 1.2)
    return (0.2 * voiced * syllable).astype(np.float32)


def test_silent_track_is_rejected_before_denoise(tmp_path):
    """트랙이 있는데 내용이 0이면 잡음 제거 전에 걸러낸다.

    2026-08-04 리허설 영상이 이 상태였다 — 295초 전체 peak 0. 원인은 젯슨의
    기본 입력 소스가 마이크가 아닌 빈 아날로그 단자였다(S15P11A301-257).
    무음을 제거해도 무음이고, 그것을 올리면 마이크 사망이 완료로 덮인다.

    모델을 쓰지 않는다 — 판정이 `denoise` 호출보다 앞이라 모델 없는 환경에서도
    이 경로가 검증된다.
    """
    source = tmp_path / "silent_event.mp4"
    make_mp4(source, np.zeros(DF_SAMPLE_RATE * 2, dtype=np.float32))

    # 트랙 자체는 정상적으로 존재한다. NoAudioTrack이 아니라는 점이 핵심이다.
    assert len(extract_audio(source)) > 0

    with pytest.raises(SilentAudioTrack):
        enhance_media(source, tmp_path / "out.m4a", quiet=True)

    # 실패했으므로 결과물을 남기지 않는다.
    assert not (tmp_path / "out.m4a").exists()


def test_silent_track_is_not_confused_with_missing_track(tmp_path):
    """두 예외는 갈라져 있어야 한다 — 하나는 정상 경로, 하나는 장치 사망이다."""
    assert not issubclass(SilentAudioTrack, NoAudioTrack)
    assert not issubclass(NoAudioTrack, SilentAudioTrack)


def test_denoise_suppresses_pure_noise():
    """소음만 있는 입력은 강하게 억제돼야 한다 (실측 -38.6dB, 여유 두고 -20dB)."""
    _require_df()
    from enhance_media import denoise

    rng = np.random.default_rng(20260803)
    noise = (0.1 * rng.standard_normal(DF_SAMPLE_RATE * 4)).astype(np.float32)

    out = denoise(noise)

    assert len(out) == len(noise)
    rms_in = float(np.sqrt(np.mean(noise**2)))
    rms_out = float(np.sqrt(np.mean(out**2)))
    assert rms_out < rms_in * 0.1  # -20dB


def test_denoise_keeps_voice_and_reduces_residual():
    _require_df()
    from enhance_media import denoise

    rng = np.random.default_rng(20260803)
    voice = voiced_like(4.0)
    noisy = np.clip(voice + (0.05 * rng.standard_normal(len(voice))).astype(np.float32), -1, 1)

    cleaned = denoise(noisy)

    assert len(cleaned) == len(noisy)
    err_before = float(np.mean((noisy - voice) ** 2))
    err_after = float(np.mean((cleaned - voice) ** 2))
    assert err_after < err_before * 0.5  # 실측 40배 개선, 여유 두고 2배


def test_atten_limit_reduces_suppression():
    """감쇠 상한을 낮추면 덜 깎여야 한다.

    실제 동시 녹음에서 무제한 감쇠가 발화의 고역까지 지웠다(잔존 0.2%). 이
    파라미터가 그 조절 수단이므로 방향이 맞는지 고정한다.
    """
    _require_df()
    from enhance_media import denoise

    rng = np.random.default_rng(20260803)
    voice = voiced_like(4.0)
    noisy = np.clip(voice + (0.05 * rng.standard_normal(len(voice))).astype(np.float32), -1, 1)

    full = denoise(noisy, atten_lim_db=None)
    limited = denoise(noisy, atten_lim_db=6.0)

    # 상한을 걸면 원본에 더 가깝게 남는다 = 입력과의 차이가 작다.
    assert float(np.mean((limited - noisy) ** 2)) < float(np.mean((full - noisy) ** 2))


def test_chunked_equals_whole_within_tolerance():
    """조각 처리가 통짜 처리와 크게 다르면 크로스페이드가 잘못된 것이다.

    기준을 출력이 아니라 입력 에너지로 나눈다 — 출력은 소음 억제로 에너지가
    작아서 비율이 불안정하다. 실측 차이는 입력 에너지의 0.004%였다.
    """
    _require_df()
    from enhance_media import denoise

    rng = np.random.default_rng(20260803)
    voice = voiced_like(8.0)
    signal = np.clip(voice + (0.05 * rng.standard_normal(len(voice))).astype(np.float32), -1, 1)

    whole = denoise(signal)
    chunked = denoise(signal, chunk_seconds=3.0, overlap_seconds=0.5)

    assert len(whole) == len(chunked)
    rel = float(np.mean((whole - chunked) ** 2)) / float(np.mean(signal**2))
    assert rel < 0.005
