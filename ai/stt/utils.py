# utils.py
"""오디오 로더. 어떤 wav든 16kHz mono float32로 통일한다."""
import librosa
from config import FS


def load_mono(path, fs=FS):
    y, _ = librosa.load(path, sr=fs, mono=True)
    return y
