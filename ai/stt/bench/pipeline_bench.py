# bench/pipeline_bench.py
"""
젯슨 측정용 다회차 벤치마크.

STT는 시나리오당 1회(음성 고정) 캐싱하고, LLM은 NUM_RUNS회 반복해
평균/최소/최대 지연과 triage 등급 일관성(%)을 집계한다.
device/compute 는 config가 자동 감지(Jetson=int8).

  cd ai/stt && python -m bench.pipeline_bench

측정 중 다른 터미널에서 `jtop` 으로 RAM/GPU/온도/전력을 함께 기록할 것.
data/ 샘플이 없으면 해당 시나리오는 NO_FILE로 스킵된다(README의 데이터 준비 참고).
결과: results/pipeline_bench_raw.csv, results/pipeline_bench_summary.csv
"""
import os
import time
import csv
from collections import Counter

import numpy as np
import torch
from faster_whisper import WhisperModel
from silero_vad import load_silero_vad, get_speech_timestamps

from sentinel_voice import config
from sentinel_voice.audio import load_mono
from sentinel_voice.config import FS
from sentinel_voice.llm import llm_extract as gms_extract
from sentinel_voice.safety import coerce_defaults, is_valid_stt, triage_rule

NUM_RUNS = int(os.getenv("BENCH_RUNS", "3"))
# 측정할 LLM 후보(GMS 모델명, 쉼표 구분). 확정 모델은 config.LLM_MODEL(gpt-5-nano)
MODELS = os.getenv("BENCH_MODELS", config.LLM_MODEL).split(",")

# (이름, 파일경로, 트리거소스). data/ 는 ai/stt 기준 상대경로.
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCENARIOS = [
    ("가스_clean", "data/clean/info.wav",              "VISION"),
    ("구조_clean", "data/clean/guzo.wav",              "VISION"),
    ("부상_clean", "data/clean/busang.wav",            "VISION"),
    ("부상_snr5",  "data/mixed/busang__moto_snr5.wav", "VISION"),
    ("부상_snr0",  "data/mixed/busang__moto_snr0.wav", "SED"),
    ("부상_snr-5", "data/mixed/busang__moto_snr-5.wav","SED"),
    ("가스_snr-5", "data/mixed/info__moto_snr-5.wav",  "SED"),
    ("침묵",       "data/clean/silence.wav",           "SED"),
]


print("=" * 60)
print(f"🚀 Sentinel 벤치마크 ({config.summary()}, runs={NUM_RUNS})")
print("=" * 60)
print("📦 STT/VAD 로딩...")
vad = load_silero_vad()
stt = WhisperModel(config.STT_MODEL, device=config.DEVICE, compute_type=config.COMPUTE)


def normalize(wav):
    rms = np.sqrt(np.mean(wav ** 2)) + 1e-9
    return np.clip(wav * (config.NORM_TARGET_RMS / rms), -1, 1).astype(np.float32)


def has_speech(wav):
    return len(get_speech_timestamps(torch.from_numpy(wav), vad, sampling_rate=FS)) > 0


def run_stt(path):
    wav = load_mono(path)
    raw_rms = float(np.sqrt(np.mean(wav ** 2)))
    if raw_rms >= config.SILENCE_RMS:
        wav = normalize(wav)
    if raw_rms < config.SILENCE_RMS or not has_speech(wav):
        return dict(status="NO_SPEECH", text="", nsp=1.0, stt_ms=0.0)
    t = time.time()
    segs, _ = stt.transcribe(wav, initial_prompt=config.STT_PROMPT, **config.STT_DECODE)
    segs = list(segs)
    stt_ms = (time.time() - t) * 1000
    text = "".join(s.text for s in segs).strip()
    nsp = float(np.mean([s.no_speech_prob for s in segs])) if segs else 1.0
    ok, why = is_valid_stt(text, nsp, config.STT_PROMPT)
    return dict(status="OK" if ok else f"INVALID:{why}", text=text, nsp=nsp, stt_ms=stt_ms)


def llm_extract(text, model):
    """GMS API 호출(지연 측정 대상). 폴백 없이 직접 호출 — 실패도 측정 결과."""
    return gms_extract(text, model=model)


def non_ok_outcome(source):
    if source == "SED":
        info = coerce_defaults({"consciousness": "무반응", "speech": "신음만", "raw_note": "무응답/무효"})
        return info, triage_rule(info)
    return None, "재질문"


# 1단계: STT 1회 캐싱
print("\n[1단계] STT 처리 및 시나리오 검사...")
list(stt.transcribe(np.zeros(FS, dtype=np.float32), language="ko"))  # warm-up
stt_cache = {}
for name, rel, source in SCENARIOS:
    path = os.path.join(BASE, rel)
    if not os.path.exists(path):
        print(f"  ⚠️ 파일 없음: {rel} (스킵)")
        stt_cache[name] = dict(status="NO_FILE", text="", nsp=1.0, stt_ms=0.0)
        continue
    c = run_stt(path)
    stt_cache[name] = c
    print(f"  {name:12s}[{source:6s}] {c['status']:14s} | STT {c['stt_ms']:5.0f}ms | ns {c['nsp']:.2f} | '{c['text']}'")

# 2단계: 다회차 LLM
raw_rows = [["model", "scenario", "run_idx", "source", "stt_status", "stt_ms", "nsp",
             "llm_ms", "consciousness", "speech", "hazard", "add_victims", "can_move", "severity", "stt_text"]]
summary_rows = [["model", "scenario", "source", "stt_status", "stt_ms", "avg_llm_ms", "min_llm_ms", "max_llm_ms",
                 "consistency_pct", "consciousness", "speech", "hazard", "add_victims", "can_move", "severity", "stt_text"]]

print(f"\n[2단계] 모델별 다회차 LLM 추론 ({NUM_RUNS}회)...")
for model in MODELS:
    model = model.strip()
    print("\n" + "=" * 50 + f"\n🧠 {model}\n" + "=" * 50)
    try:
        llm_extract("테스트", model)  # warm-up
    except Exception as e:
        print(f"  ⚠️ warm-up 실패: {e}")

    for name, rel, source in SCENARIOS:
        c = stt_cache[name]
        if c["status"] != "OK":
            info, sev = non_ok_outcome(source)
            cons = info["consciousness"] if info else "-"
            spe = info["speech"] if info else "-"
            cm = info["can_move"] if info else "-"
            hz = "|".join(info["hazard"]) if info else "-"
            av = info["additional_victims"] if info else "-"
            raw_rows.append([model, name, 1, source, c["status"], round(c["stt_ms"], 1), round(c["nsp"], 3),
                             0.0, cons, spe, hz, av, cm, sev, c["text"]])
            summary_rows.append([model, name, source, c["status"], round(c["stt_ms"], 1), 0.0, 0.0, 0.0,
                                 100.0, cons, spe, hz, av, cm, sev, c["text"]])
            print(f"  {name:12s}[{source:6s}] {c['status']:12s} ➔ 폴백: {sev}")
            continue

        times, sevs, infos = [], [], []
        for run_idx in range(1, NUM_RUNS + 1):
            t = time.time()
            try:
                info = llm_extract(c["text"], model)
                sev = triage_rule(info)
            except Exception as e:
                info = coerce_defaults({})
                sev = f"LLM오류:{e}"
            llm_ms = (time.time() - t) * 1000
            times.append(llm_ms)
            sevs.append(sev)
            infos.append(info)
            raw_rows.append([model, name, run_idx, source, c["status"], round(c["stt_ms"], 1), round(c["nsp"], 3),
                             round(llm_ms, 1), info["consciousness"], info["speech"],
                             "|".join(info["hazard"]), info["additional_victims"], info["can_move"], sev, c["text"]])

        avg, mn, mx = float(np.mean(times)), float(np.min(times)), float(np.max(times))
        top_sev, cnt = Counter(sevs).most_common(1)[0]
        consistency = cnt / NUM_RUNS * 100.0
        rep = infos[sevs.index(top_sev)]
        summary_rows.append([model, name, source, c["status"], round(c["stt_ms"], 1),
                             round(avg, 1), round(mn, 1), round(mx, 1), round(consistency, 1),
                             rep["consciousness"], rep["speech"], "|".join(rep["hazard"]),
                             rep["additional_victims"], rep["can_move"], top_sev, c["text"]])
        print(f"  {name:12s}[{source:6s}] LLM평균 {avg:5.0f}ms (min {mn:.0f}/max {mx:.0f}) | 일관성 {consistency:3.0f}% ➔ {top_sev}")

# 3단계: 저장
os.makedirs(os.path.join(BASE, "results"), exist_ok=True)
with open(os.path.join(BASE, "results/pipeline_bench_raw.csv"), "w", newline="", encoding="utf-8") as f:
    csv.writer(f).writerows(raw_rows)
with open(os.path.join(BASE, "results/pipeline_bench_summary.csv"), "w", newline="", encoding="utf-8") as f:
    csv.writer(f).writerows(summary_rows)

print("\n✅ 완료 → results/pipeline_bench_raw.csv, results/pipeline_bench_summary.csv")
