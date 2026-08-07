"""스레드 풀 상한이 탐지 CPU와 처리율에 미치는 영향 측정 (S15P11A301-328 후속).

배경: 통합 부하에서 탐지 프로세스가 코어 2.3~4.3개를 쓰는데 GPU는 14~18%다.
스레드별로 쪼개 보면 메인 68%에 워커 다섯이 각 17%로, 코어 수(6)에 맞춘 스레드
풀이 CPU 병렬 작업을 하고 있다. 추론은 TensorRT 엔진으로 GPU에서 돌므로 그
CPU는 전처리·후처리 몫이다.

OpenCV와 PyTorch는 기본적으로 코어 수만큼 스레드를 쓴다. 그런데 이 워크로드의
CPU 구간은 640x640 한 장 수준의 작은 연산이라 병렬화 이득보다 동기화 비용이
클 수 있고, 6코어를 SLAM·Nav2·스트리밍과 나눠 쓰는 상황에서는 경합만 늘린다.

사용법:
    .venv/bin/python -m scripts.bench_threads --threads 0   # 제한 없음(현행)
    .venv/bin/python -m scripts.bench_threads --threads 2
"""

from __future__ import annotations

import argparse
import os
import time


def _limit_threads(n: int) -> None:
    """스레드 상한을 건다. import보다 먼저 환경변수를 세워야 듣는다."""
    for var in (
        "OMP_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "MKL_NUM_THREADS",
        "NUMEXPR_NUM_THREADS",
    ):
        os.environ[var] = str(n)


def _proc_cpu_seconds() -> float:
    with open("/proc/self/stat", encoding="ascii") as handle:
        fields = handle.readline().split()
    # utime + stime, USER_HZ 단위.
    return (int(fields[13]) + int(fields[14])) / os.sysconf("SC_CLK_TCK")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--threads", type=int, default=0,
                        help="0이면 제한하지 않는다(현행 동작)")
    parser.add_argument("--model", default="models/yolo26n.engine")
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--frames", type=int, default=120)
    parser.add_argument("--warmup", type=int, default=20)
    args = parser.parse_args()

    if args.threads > 0:
        _limit_threads(args.threads)

    import cv2
    import numpy as np
    import torch
    from ultralytics import YOLO

    if args.threads > 0:
        cv2.setNumThreads(args.threads)
        torch.set_num_threads(args.threads)

    print(f"스레드 설정: 요청={args.threads or '제한없음'} "
          f"cv2={cv2.getNumThreads()} torch={torch.get_num_threads()} "
          f"코어={os.cpu_count()}")

    model = YOLO(args.model, task="detect")

    # 실제 입력과 같은 크기·형식. 매번 같은 프레임을 쓰면 캐시 효과가 생길 수
    # 있으므로 몇 장을 돌려 쓴다. 내용은 결과에 영향이 없다(CPU 경로 측정).
    rng = np.random.default_rng(0)
    frames = [rng.integers(0, 255, (720, 1280, 3), dtype=np.uint8) for _ in range(8)]

    for i in range(args.warmup):
        model.predict(frames[i % len(frames)], imgsz=args.imgsz,
                      device=0, verbose=False)

    cpu0 = _proc_cpu_seconds()
    wall0 = time.monotonic()
    for i in range(args.frames):
        model.predict(frames[i % len(frames)], imgsz=args.imgsz,
                      device=0, verbose=False)
    wall = time.monotonic() - wall0
    cpu = _proc_cpu_seconds() - cpu0

    print(f"  프레임 {args.frames}장 / 벽시계 {wall:.2f}초 → {args.frames / wall:.2f} FPS")
    print(f"  CPU 시간 {cpu:.2f}초 → 코어 점유 {cpu / wall * 100:.0f}%")
    print(f"  프레임당 CPU {cpu / args.frames * 1000:.1f}ms")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
