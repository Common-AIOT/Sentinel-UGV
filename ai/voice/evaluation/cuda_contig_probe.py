"""스택 부하 상태에서 CUDA 연속 버퍼 할당 한계를 측정한다.

근거: 2026-07-24 실측에서 available 5.0GB 상태에서도 CUDA가 1.79GB 연속
버퍼 할당에 실패해 로컬 3B LLM 로드가 죽었다(docs/08-AI-음성.md 33.13.5).
이 프로브는 그 관측을 임의 시점에 재현 가능한 수치로 만든다. 내림차순
크기로 단일 연속 CUDA 텐서 할당을 시도해 성공 최대 크기와 실패 크기를
기록하고, 시도 전후 /proc/meminfo 스냅샷을 함께 남긴다.

Jetson 실행(음성 venv에 torch가 있다):

    cd /home/orin/projects/S15P11A301/ai/voice
    /home/orin/projects/S15P11A301/.venv/bin/python -m evaluation.cuda_contig_probe \
        --label fullstack --output fullstack-probe.json

안전장치: MemAvailable에서 안전 여유(기본 768MB)를 뺀 값보다 큰 크기는
시도하지 않고 skipped_safety로 기록한다. 스택 프로세스를 OOM killer에
빼앗기지 않기 위한 것으로, "그 크기는 시도조차 불가능했다"는 것 자체가
측정 결과다.

CUDA가 없는 PC에서는 --self-test로 판정 로직만 검증한다. 같은 검증이
tests/test_cuda_contig_probe.py에도 있다.

실행 절차는 ai/voice/docs/fullstack-ram-probe-runbook.md를 따른다.
"""

from __future__ import annotations

import argparse
import datetime
import gc
import json
import sys
from pathlib import Path

MB = 1024 * 1024

# 2,400MB = 로컬 3B LLM 추론 피크 증분, 1,792MB = 과거 실패 관측치(1.79GB)
ATTEMPT_SIZES_MB = [2400, 2048, 1792, 1536, 1280, 1024, 768, 512, 384, 256, 128]
SAFETY_MARGIN_MB = 768


def read_meminfo() -> dict[str, int]:
    """/proc/meminfo에서 MB 단위 주요 값을 읽는다. 리눅스가 아니면 빈 dict."""
    path = Path("/proc/meminfo")
    if not path.exists():
        return {}
    wanted = {"MemTotal", "MemFree", "MemAvailable", "Buffers", "Cached", "SwapFree"}
    result: dict[str, int] = {}
    for line in path.read_text(encoding="ascii").splitlines():
        key, _, rest = line.partition(":")
        if key in wanted:
            result[key + "_mb"] = int(rest.split()[0]) // 1024
    return result


def run_probe(
    allocate_mb,
    sizes_mb: list[int],
    available_mb: int | None,
    safety_margin_mb: int,
) -> dict:
    """할당 함수를 받아 크기별 시도 결과와 최대 성공 크기를 돌려준다.

    allocate_mb(size)는 성공 시 None, 실패 시 예외를 던져야 한다.
    """
    attempts = []
    max_ok_mb = 0
    for size in sizes_mb:
        if (
            available_mb is not None
            and size > available_mb - safety_margin_mb
        ):
            attempts.append({"size_mb": size, "status": "skipped_safety"})
            continue
        try:
            allocate_mb(size)
            attempts.append({"size_mb": size, "status": "ok"})
            max_ok_mb = max(max_ok_mb, size)
            break  # 내림차순이므로 첫 성공이 최대 연속 할당 크기다
        except Exception as exc:  # noqa: BLE001 - 실패 사유 원문 보존이 목적
            attempts.append(
                {"size_mb": size, "status": "failed", "error": repr(exc)}
            )
    failed = [a["size_mb"] for a in attempts if a["status"] == "failed"]
    skipped = [a["size_mb"] for a in attempts if a["status"] == "skipped_safety"]
    return {
        "attempts": attempts,
        "max_ok_mb": max_ok_mb,
        "min_failed_mb": min(failed) if failed else None,
        "skipped_safety_mb": skipped,
    }


def cuda_allocate_factory():
    import torch

    if not torch.cuda.is_available():
        raise SystemExit("CUDA를 사용할 수 없다. Jetson venv에서 실행해야 한다.")

    def allocate(size_mb: int) -> None:
        tensor = torch.empty(size_mb * MB, dtype=torch.uint8, device="cuda")
        tensor.zero_()  # 실제 물리 페이지가 잡히는지까지 확인한다
        del tensor
        gc.collect()
        torch.cuda.empty_cache()

    device = torch.cuda.get_device_name(0)
    return allocate, {"torch": torch.__version__, "device": device}


def self_test() -> int:
    """1,000MB 위에서만 실패하는 가짜 할당기로 판정 로직을 검증한다."""

    def fake_allocate(size_mb: int) -> None:
        if size_mb > 1000:
            raise RuntimeError(f"fake OOM at {size_mb}MB")

    result = run_probe(
        fake_allocate, ATTEMPT_SIZES_MB, available_mb=3000, safety_margin_mb=768
    )
    assert result["skipped_safety_mb"] == [2400], result
    assert result["min_failed_mb"] == 1024, result
    assert result["max_ok_mb"] == 768, result

    # 안전 여유 때문에 큰 크기를 아예 시도 못 하는 경우
    result = run_probe(
        fake_allocate, ATTEMPT_SIZES_MB, available_mb=900, safety_margin_mb=768
    )
    assert result["max_ok_mb"] == 128, result
    assert result["skipped_safety_mb"][0] == 2400, result

    print("self-test OK")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--label", default="probe", help="예: idle, fullstack")
    parser.add_argument("--output", type=Path, help="결과 JSON 경로")
    parser.add_argument("--safety-margin-mb", type=int, default=SAFETY_MARGIN_MB)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test()

    meminfo_before = read_meminfo()
    allocate, runtime_info = cuda_allocate_factory()
    probe = run_probe(
        allocate,
        ATTEMPT_SIZES_MB,
        available_mb=meminfo_before.get("MemAvailable_mb"),
        safety_margin_mb=args.safety_margin_mb,
    )
    result = {
        "label": args.label,
        "measured_at": datetime.datetime.now().astimezone().isoformat(),
        "runtime": runtime_info,
        "meminfo_before": meminfo_before,
        **probe,
        "meminfo_after": read_meminfo(),
    }
    text = json.dumps(result, ensure_ascii=False, indent=2)
    print(text)
    if args.output:
        args.output.write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
