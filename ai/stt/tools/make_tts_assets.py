"""
고정 안내 문구 사전녹음 wav 생성기 (개발 PC 전용 — MeloTTS 필요).

젯슨은 MeloTTS를 탑재하지 않고(RAM 절약 1순위, docs/메모리-예산.md §5)
여기서 만든 wav 를 assets/ 에서 재생만 한다. 생성 후 assets/*.wav 를 커밋할 것.

  cd ai/stt && python -m tools.make_tts_assets
"""
import os

from melo.api import TTS

from sentinel_voice import config

ASSETS = config.STT_ROOT / "assets"
os.makedirs(ASSETS, exist_ok=True)

tts = TTS(language=config.TTS_LANG, device=config.DEVICE)
sid = tts.hps.data.spk2id[config.TTS_LANG]

for text, fname in config.GUIDE_WAVS.items():
    path = ASSETS / fname
    tts.tts_to_file(text, sid, str(path), speed=0.9)
    print(f"생성: {fname}  ← '{text}'")

print(f"\n완료 — {ASSETS} 의 wav {len(config.GUIDE_WAVS)}개를 커밋하세요.")
