"""Jetson 음성·통합 벤치의 tegrastats 자원 사용량을 기록한다.

사용 예:
    python -m evaluation.jetson_resource_bench \
      --label voice-gms \
      --output-dir results/jetson-resource/voice-gms \
      -- python -u -m evaluation.pipeline_bench

측정 구간은 baseline -> command -> after로 구분한다. tegrastats 원문과
구조화 CSV, 요약 JSON을 함께 남기며 API 키나 환경변수 값은 기록하지 않는다.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import platform
import re
import statistics
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable


RAM_RE = re.compile(r"\bRAM\s+(\d+)/(\d+)MB\b")
SWAP_RE = re.compile(r"\bSWAP\s+(\d+)/(\d+)MB\b")
CPU_RE = re.compile(r"\bCPU\s+\[([^\]]+)\]")
GPU_RE = re.compile(r"\bGR3D_FREQ\s+(\d+)%")
TEMP_RE = re.compile(r"\b[A-Za-z0-9_]+@(-?\d+(?:\.\d+)?)C\b")


@dataclass(frozen=True)
class ResourceSample:
    elapsed_s: float
    phase: str
    ram_used_mb: int
    ram_total_mb: int
    mem_available_mb: float | None
    swap_used_mb: int | None
    swap_total_mb: int | None
    cpu_avg_pct: float | None
    gpu_pct: int | None
    temperature_max_c: float | None
    raw: str


def _mean(values: Iterable[float]) -> float | None:
    items = list(values)
    return round(statistics.fmean(items), 2) if items else None


def parse_tegrastats_line(
    line: str,
    *,
    elapsed_s: float,
    phase: str,
    mem_available_mb: float | None = None,
) -> ResourceSample | None:
    """tegrastats 한 줄을 구조화한다. RAM 필드가 없으면 측정 샘플이 아니다."""
    ram = RAM_RE.search(line)
    if not ram:
        return None

    swap = SWAP_RE.search(line)
    gpu = GPU_RE.search(line)
    temps = [float(value) for value in TEMP_RE.findall(line)]

    cpu_values: list[float] = []
    cpu = CPU_RE.search(line)
    if cpu:
        for token in cpu.group(1).split(","):
            match = re.search(r"(\d+(?:\.\d+)?)%", token)
            if match:
                cpu_values.append(float(match.group(1)))

    return ResourceSample(
        elapsed_s=round(elapsed_s, 3),
        phase=phase,
        ram_used_mb=int(ram.group(1)),
        ram_total_mb=int(ram.group(2)),
        mem_available_mb=mem_available_mb,
        swap_used_mb=int(swap.group(1)) if swap else None,
        swap_total_mb=int(swap.group(2)) if swap else None,
        cpu_avg_pct=_mean(cpu_values),
        gpu_pct=int(gpu.group(1)) if gpu else None,
        temperature_max_c=max(temps) if temps else None,
        raw=line.strip(),
    )


def read_mem_available_mb(path: Path = Path("/proc/meminfo")) -> float | None:
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("MemAvailable:"):
                return round(int(line.split()[1]) / 1024, 2)
    except (OSError, ValueError, IndexError):
        return None
    return None


def summarize(samples: list[ResourceSample], *, label: str, command: list[str],
              command_exit_code: int, command_elapsed_s: float) -> dict:
    if not samples:
        raise ValueError("유효한 tegrastats RAM 샘플이 없습니다.")

    by_phase = {
        phase: [sample for sample in samples if sample.phase == phase]
        for phase in ("baseline", "command", "after")
    }

    def median_ram(phase: str) -> float | None:
        values = [sample.ram_used_mb for sample in by_phase[phase]]
        return round(statistics.median(values), 2) if values else None

    command_samples = by_phase["command"] or samples
    after_tail = by_phase["after"][-5:]
    available = [
        sample.mem_available_mb
        for sample in command_samples
        if sample.mem_available_mb is not None
    ]
    swaps = [
        sample.swap_used_mb
        for sample in command_samples
        if sample.swap_used_mb is not None
    ]
    temperatures = [
        sample.temperature_max_c
        for sample in command_samples
        if sample.temperature_max_c is not None
    ]
    cpus = [
        sample.cpu_avg_pct
        for sample in command_samples
        if sample.cpu_avg_pct is not None
    ]
    gpus = [
        sample.gpu_pct
        for sample in command_samples
        if sample.gpu_pct is not None
    ]

    baseline_ram = median_ram("baseline")
    peak_ram = max(sample.ram_used_mb for sample in command_samples)
    after_ram = (
        round(statistics.median(sample.ram_used_mb for sample in after_tail), 2)
        if after_tail else None
    )
    baseline_swap_values = [
        sample.swap_used_mb
        for sample in by_phase["baseline"]
        if sample.swap_used_mb is not None
    ]
    after_swap_values = [
        sample.swap_used_mb
        for sample in after_tail
        if sample.swap_used_mb is not None
    ]

    return {
        "schemaVersion": "jetson-resource-v1.0",
        "label": label,
        "command": command,
        "commandExitCode": command_exit_code,
        "commandElapsedSeconds": round(command_elapsed_s, 3),
        "sampleCount": len(samples),
        "phaseSampleCounts": {
            phase: len(phase_samples) for phase, phase_samples in by_phase.items()
        },
        "ramTotalMb": samples[0].ram_total_mb,
        "ramBaselineMb": baseline_ram,
        "ramPeakMb": peak_ram,
        "ramIncreaseFromBaselineMb": (
            round(peak_ram - baseline_ram, 2) if baseline_ram is not None else None
        ),
        "ramAfterMb": after_ram,
        "ramRetainedAfterMb": (
            round(after_ram - baseline_ram, 2)
            if after_ram is not None and baseline_ram is not None else None
        ),
        "memAvailableMinimumMb": min(available) if available else None,
        "swapBaselineMb": (
            round(statistics.median(baseline_swap_values), 2)
            if baseline_swap_values else None
        ),
        "swapPeakMb": max(swaps) if swaps else None,
        "swapAfterMb": (
            round(statistics.median(after_swap_values), 2)
            if after_swap_values else None
        ),
        "temperatureMaxC": max(temperatures) if temperatures else None,
        "cpuAveragePct": _mean(cpus),
        "gpuAveragePct": _mean(gpus),
        "gpuPeakPct": max(gpus) if gpus else None,
        "passed": command_exit_code == 0 and (
            min(available) >= 1024 if available else True
        ),
        "failureReasons": (
            ([] if command_exit_code == 0 else [f"command_exit_{command_exit_code}"])
            + (
                ["available_ram_below_1024mb"]
                if available and min(available) < 1024 else []
            )
        ),
    }


def write_outputs(output_dir: Path, samples: list[ResourceSample], summary: dict) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    raw_log = output_dir / "tegrastats.log"
    raw_log.write_text(
        "".join(f"{sample.elapsed_s:.3f}\t{sample.phase}\t{sample.raw}\n" for sample in samples),
        encoding="utf-8",
    )

    fieldnames = list(asdict(samples[0]).keys())
    with (output_dir / "resource-samples.csv").open(
        "w", newline="", encoding="utf-8-sig"
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(asdict(sample) for sample in samples)

    (output_dir / "resource-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Jetson tegrastats와 실행 명령을 한 runId로 기록합니다."
    )
    parser.add_argument("--label", required=True, help="예: voice-gms")
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--baseline-seconds", type=int, default=10)
    parser.add_argument("--after-seconds", type=int, default=60)
    parser.add_argument("--interval-ms", type=int, default=1000)
    parser.add_argument(
        "command",
        nargs=argparse.REMAINDER,
        help="-- 뒤에 실행할 명령을 입력합니다.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        raise SystemExit("-- 뒤에 측정할 명령이 필요합니다.")
    if platform.system() != "Linux" or not Path("/etc/nv_tegra_release").exists():
        raise SystemExit("이 도구는 NVIDIA Jetson Linux에서 실행해야 합니다.")

    phase = {"value": "baseline"}
    samples: list[ResourceSample] = []
    start = time.monotonic()
    tegrastats = subprocess.Popen(
        ["tegrastats", "--interval", str(args.interval_ms)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    def collect() -> None:
        assert tegrastats.stdout is not None
        for line in tegrastats.stdout:
            sample = parse_tegrastats_line(
                line,
                elapsed_s=time.monotonic() - start,
                phase=phase["value"],
                mem_available_mb=read_mem_available_mb(),
            )
            if sample:
                samples.append(sample)

    collector = threading.Thread(target=collect, daemon=True)
    collector.start()
    exit_code = 130
    command_elapsed = 0.0
    try:
        time.sleep(args.baseline_seconds)
        phase["value"] = "command"
        command_start = time.monotonic()
        completed = subprocess.run(command, check=False)
        command_elapsed = time.monotonic() - command_start
        exit_code = completed.returncode
        phase["value"] = "after"
        time.sleep(args.after_seconds)
    except KeyboardInterrupt:
        print("\n[WARN] 사용자 중단", file=sys.stderr)
    finally:
        tegrastats.terminate()
        try:
            tegrastats.wait(timeout=5)
        except subprocess.TimeoutExpired:
            tegrastats.kill()
        collector.join(timeout=5)

    summary = summarize(
        samples,
        label=args.label,
        command=command,
        command_exit_code=exit_code,
        command_elapsed_s=command_elapsed,
    )
    summary["gitCommit"] = _git_commit()
    summary["device"] = platform.machine()
    write_outputs(args.output_dir, samples, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"\n결과 저장: {args.output_dir}")
    return 0 if summary["passed"] else 1


def _git_commit() -> str | None:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


if __name__ == "__main__":
    raise SystemExit(main())
