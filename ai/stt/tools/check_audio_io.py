"""Jetson 음성 입출력 장치 점검 및 증적 수집 도구.

기본 실행은 장치 목록만 조회한다. ``--record-seconds``를 지정하면 입력 장치를
녹음하고, 선택적으로 같은 샘플을 블루투스 스피커로 재생한다.

입력 장치 기본값은 플랫폼마다 다르다 — 윈도우는 ``BRIO``, 리눅스는 ``pulse``다.
리눅스에서 이름으로 USB 마이크를 지목할 수 없는 이유는 ``DEFAULT_INPUT_MATCH``
주석에 있다(S15P11A301-257).

녹음 결과가 전 구간 디지털 무음이면 **실패로 끝낸다.** 조용한 것이 아니라 마이크가
아닌 경로를 읽고 있다는 뜻이다.
"""

from __future__ import annotations

import argparse
import json
import math
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import sounddevice as sd
import soundfile as sf

from sentinel_voice import config


DEFAULT_SAMPLE_RATE = 16_000

# 입력 장치를 이름으로 지목할 수 있는가는 플랫폼마다 다르다.
#
# 윈도우에서는 PortAudio가 `Microphone (BRIO 100)`처럼 장치명을 그대로 노출한다.
# 젯슨(리눅스)에서는 노출하지 않는다 — PulseAudio가 USB 카드를 독점하면 ALSA
# 직접 접근(`hw:0`)이 막히고, PortAudio 목록에 그 카드가 **아예 나타나지 않는다.**
# 2026-08-04 실측에서 `arecord -l`은 `card 0: B100 [Brio 100]`을 보여주는데
# PortAudio 목록은 `hw:1`(HDMI)부터 시작했다(S15P11A301-257).
#
# 그래서 리눅스 기본값을 `BRIO`로 두면 이 도구는 젯슨에서 **항상 실패한다.**
# PulseAudio를 거치는 `pulse`를 기본으로 쓰고, 어느 소스로 가는지는 PulseAudio가
# 정한다(`pactl info`의 Default Source · `PULSE_SOURCE`).
DEFAULT_INPUT_MATCH = "BRIO" if platform.system() == "Windows" else "pulse"

# 위 기본값이 없는 환경을 위한 폴백 순서. 앞에서부터 먼저 잡히는 것을 쓴다.
INPUT_FALLBACKS = ("pulse", "default")


def _device_rows() -> list[dict[str, Any]]:
    rows = []
    for index, device in enumerate(sd.query_devices()):
        rows.append(
            {
                "index": index,
                "name": str(device["name"]),
                "hostapi": int(device["hostapi"]),
                "max_input_channels": int(device["max_input_channels"]),
                "max_output_channels": int(device["max_output_channels"]),
                "default_samplerate": float(device["default_samplerate"]),
            }
        )
    return rows


def _matching_device(
    devices: list[dict[str, Any]], name_part: str, channel_key: str
) -> dict[str, Any]:
    needle = name_part.casefold()
    matches = [
        device
        for device in devices
        if needle in device["name"].casefold() and device[channel_key] > 0
    ]
    if not matches:
        hint = ""
        if channel_key == "max_input_channels" and platform.system() != "Windows":
            hint = (
                "\n  리눅스에서는 PulseAudio가 USB 카드를 독점해 PortAudio가 그"
                " 장치를 이름으로 노출하지 않습니다."
                f"\n  대신 {' 또는 '.join(INPUT_FALLBACKS)}를 지정하고, 어느"
                " 마이크로 갈지는 PulseAudio에서 정하세요."
                "\n    pactl info | grep -i 'default source'"
                "\n    pactl set-default-source <소스 이름>"
                "\n  (S15P11A301-257)"
            )
        raise RuntimeError(
            f"'{name_part}' 문자열과 일치하는 {channel_key} 장치를 찾지"
            f" 못했습니다.{hint}"
        )
    if len(matches) > 1:
        candidates = ", ".join(
            f"{device['index']}:{device['name']}" for device in matches
        )
        raise RuntimeError(
            f"장치명이 모호합니다({candidates}). 더 구체적인 문자열을 지정하세요."
        )
    return matches[0]


def _git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _record_metrics(samples: np.ndarray) -> dict[str, float]:
    absolute = np.abs(samples)
    rms = float(np.sqrt(np.mean(np.square(samples), dtype=np.float64)))
    peak = float(np.max(absolute))
    clipped_ratio = float(np.mean(absolute >= 0.99))
    rms_dbfs = 20 * math.log10(max(rms, 1e-12))
    peak_dbfs = 20 * math.log10(max(peak, 1e-12))
    return {
        "rms": round(rms, 6),
        "rms_dbfs": round(rms_dbfs, 2),
        "peak": round(peak, 6),
        "peak_dbfs": round(peak_dbfs, 2),
        "clipped_ratio": round(clipped_ratio, 6),
    }


def _print_devices(devices: list[dict[str, Any]]) -> None:
    print("index | input | output | default Hz | name")
    print("-" * 78)
    for device in devices:
        print(
            f"{device['index']:>5} | "
            f"{device['max_input_channels']:>5} | "
            f"{device['max_output_channels']:>6} | "
            f"{device['default_samplerate']:>10.0f} | "
            f"{device['name']}"
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input-match",
        default=DEFAULT_INPUT_MATCH,
        help=(
            f"입력 장치명에 포함될 문자열(이 환경 기본: {DEFAULT_INPUT_MATCH})."
            " 리눅스에서는 PortAudio가 USB 마이크를 이름으로 노출하지 않으므로"
            " pulse를 거친다"
        ),
    )
    parser.add_argument(
        "--output-match",
        help="출력 장치명에 포함될 문자열(블루투스 스피커 이름 권장)",
    )
    parser.add_argument(
        "--sample-rate", type=int, default=DEFAULT_SAMPLE_RATE, help="녹음 Hz"
    )
    parser.add_argument(
        "--record-seconds",
        type=float,
        default=0,
        help="0보다 크면 해당 시간만큼 mono 녹음",
    )
    parser.add_argument(
        "--playback",
        action="store_true",
        help="녹음 결과를 선택한 출력 장치로 재생",
    )
    parser.add_argument(
        "--wav",
        type=Path,
        default=Path.home() / "audio_io_sample.wav",
        help="녹음 WAV 저장 경로",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=Path.home() / "audio_io_report.json",
        help="JSON 증적 저장 경로",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.record_seconds < 0:
        raise ValueError("--record-seconds는 0 이상이어야 합니다.")
    if args.playback and args.record_seconds <= 0:
        raise ValueError("--playback은 --record-seconds와 함께 사용하세요.")
    if args.playback and not args.output_match:
        raise ValueError("--playback 사용 시 --output-match가 필요합니다.")

    devices = _device_rows()
    _print_devices(devices)

    report: dict[str, Any] = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit": _git_commit(),
        "platform": platform.platform(),
        "sample_rate_hz": args.sample_rate,
        "input_match": args.input_match,
        "output_match": args.output_match,
        "devices": devices,
        "recording": None,
        "playback_requested": args.playback,
    }

    input_device = _matching_device(
        devices, args.input_match, "max_input_channels"
    )
    sd.check_input_settings(
        device=input_device["index"],
        channels=1,
        dtype="float32",
        samplerate=args.sample_rate,
    )
    report["selected_input"] = input_device
    print(f"\n[OK] 입력 장치: {input_device['index']} {input_device['name']}")
    print(f"[OK] 입력 형식: mono / float32 / {args.sample_rate}Hz")

    output_device = None
    if args.output_match:
        output_device = _matching_device(
            devices, args.output_match, "max_output_channels"
        )
        sd.check_output_settings(
            device=output_device["index"],
            channels=1,
            dtype="float32",
            samplerate=args.sample_rate,
        )
        report["selected_output"] = output_device
        print(f"[OK] 출력 장치: {output_device['index']} {output_device['name']}")
        print(f"[OK] 출력 형식: mono / float32 / {args.sample_rate}Hz")

    if args.record_seconds > 0:
        frame_count = int(args.record_seconds * args.sample_rate)
        print(f"\n[REC] {args.record_seconds:.1f}초 동안 말하세요.")
        samples = sd.rec(
            frame_count,
            samplerate=args.sample_rate,
            channels=1,
            dtype="float32",
            device=input_device["index"],
        )
        sd.wait()
        samples = samples.reshape(-1)
        args.wav.parent.mkdir(parents=True, exist_ok=True)
        sf.write(args.wav, samples, args.sample_rate, subtype="PCM_16")
        metrics = _record_metrics(samples)
        report["recording"] = {
            "duration_seconds": args.record_seconds,
            "wav_path": str(args.wav.resolve()),
            **metrics,
        }
        print(
            f"[OK] 녹음 저장: {args.wav} "
            f"(RMS {metrics['rms_dbfs']}dBFS, peak {metrics['peak_dbfs']}dBFS)"
        )
        # 디지털 무음은 "조용했다"가 아니라 "마이크가 아닌 것을 읽었다"는 뜻이다.
        # 이 판정이 없어서 2026-08-04까지 빈 입력을 5분 녹음한 사실을 아무도
        # 몰랐다. 여기서 바로 반환하지 않는다 — 증적 JSON을 남겨야 진단이 된다.
        silent_input = metrics["peak"] <= config.SILENT_INPUT_PEAK
        report["recording"]["silent_input"] = silent_input
        if silent_input:
            print(
                "[FAIL] 전 구간이 디지털 무음입니다(peak 0). 조용한 것이 아니라"
                " 마이크가 아닌 경로를 읽고 있습니다.\n"
                "       pactl info | grep -i 'default source' 로 기본 소스를"
                " 확인하고 실제 마이크로 바꾸세요. (S15P11A301-257)",
                file=sys.stderr,
            )

        if args.playback:
            assert output_device is not None
            print("[PLAY] 녹음 결과를 재생합니다.")
            sd.play(
                samples,
                samplerate=args.sample_rate,
                device=output_device["index"],
            )
            sd.wait()
            print("[OK] 재생 API 완료")

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"[OK] JSON 증적 저장: {args.report}")
    # 디지털 무음이면 증적을 남긴 뒤에 실패로 끝낸다.
    return 1 if (report.get("recording") or {}).get("silent_input") else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        raise SystemExit(1)
