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


def normalize(wav):
    """목표 RMS로 맞춘다. 노이즈도 함께 증폭되므로 무음 판정 뒤에만 호출한다."""
    scale = config.NORM_TARGET_RMS / (rms(wav) + 1e-9)
    return np.clip(np.asarray(wav, dtype=np.float32) * scale, -1, 1).astype(np.float32)
