"""오디오 로더와 레벨 처리. 어떤 wav든 16kHz mono float32로 통일한다."""
import librosa
import numpy as np

from . import config
from .config import FS


def load_mono(path, fs=FS):
    y, _ = librosa.load(path, sr=fs, mono=True)
    return y


def rms(wav) -> float:
    """정규화 전 원본 레벨. 무음 판정은 반드시 이 값으로 한다."""
    if wav is None or len(wav) == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.asarray(wav, dtype=np.float32) ** 2)))


def peak(wav) -> float:
    """정규화 전 원본의 최대 절댓값. 캡처 경로 사망 판정에 쓴다.

    무음 판정(`rms`)과 목적이 다르다. rms는 "조용한가"를, peak는 "신호가 아예
    없는가"를 본다. 살아 있는 마이크는 조용해도 peak가 0이 아니다.
    """
    if wav is None or len(wav) == 0:
        return 0.0
    return float(np.max(np.abs(np.asarray(wav, dtype=np.float32))))


def is_silent_input(wav, threshold=None) -> bool:
    """입력이 디지털 무음인가 — 마이크가 아니라 빈 경로를 읽고 있는가.

    근거와 실제 사례는 `config.SILENT_INPUT_PEAK` 주석에 있다(S15P11A301-257).
    """
    limit = config.SILENT_INPUT_PEAK if threshold is None else threshold
    return peak(wav) <= limit


def normalize(wav):
    """목표 RMS로 맞춘다. 노이즈도 함께 증폭되므로 무음 판정 뒤에만 호출한다."""
    scale = config.NORM_TARGET_RMS / (rms(wav) + 1e-9)
    return np.clip(np.asarray(wav, dtype=np.float32) * scale, -1, 1).astype(np.float32)
