#!/usr/bin/env python3
"""PoC-B 부하 주입 — YOLO 추론 상당 GPU/CPU 점유 (S15P11A301-62 합격 조건 5).

합격 조건 5는 "YOLO 몫 CPU 헤드룸이 정량적으로 남아 있음"이다. 이를 재려면
실제 추론 부하를 동시에 걸어야 한다. 이 스크립트가 그 부하를 만든다.

TensorRT 미변환 상태의 PyTorch(.pt) 추론을 사용한다. TensorRT보다 CPU
전처리와 커널 런치 오버헤드가 크므로 **실제 운용보다 비관적인 부하**다.
따라서 이 조건에서 스트리밍이 목표 FPS를 지키면 그것은 상한이 아니라
하한 보장이다. 반대로 미달하면 TensorRT 변환 후 재측정해야 하며 조건 5는
미확정으로 남는다.

프레임은 합성 이미지를 쓴다. 카메라는 usb_cam이 단독 점유하고, 이 스크립트의
목적은 영상 내용이 아니라 추론 부하 자체이기 때문이다.

사용법:
    ./poc_b_yolo_load.py --seconds 600 --target-fps 15 --out /tmp/poc_b/yolo.json
"""

import argparse
import json
import statistics
import sys
import time

import numpy as np

MODEL_DEFAULT = '/home/orin/projects/S15P11A301/jetson/models/yolo26n.pt'


def build_frames(count: int, height: int, width: int, seed: int = 0) -> list:
    """추론 입력용 합성 프레임. 매 호출 난수를 만들면 그 비용이 부하에 섞이므로
    미리 만들어 두고 순환한다."""
    rng = np.random.default_rng(seed)
    return [
        (rng.random((height, width, 3)) * 255).astype(np.uint8)
        for _ in range(count)
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--model', default=MODEL_DEFAULT)
    parser.add_argument('--seconds', type=float, default=600.0)
    parser.add_argument('--target-fps', type=float, default=15.0,
                        help='명세 25.5의 Detect 상시 추론 주기')
    parser.add_argument('--imgsz', type=int, default=640)
    parser.add_argument('--precision', choices=('fp16', 'fp32'), default='fp16',
                        help='Orin Nano에서 FP32는 메모리 여유가 적을 때 '
                             'cuBLAS 핸들 할당(CUBLAS_STATUS_ALLOC_FAILED)에 실패한다')
    parser.add_argument('--out')
    args = parser.parse_args()

    from ultralytics import YOLO
    import torch

    model = YOLO(args.model)
    frames = build_frames(8, 720, 1280)

    # ultralytics 8.4에서 half/int8은 quantize로 통합됐다(16 = FP16, 32 = FP32).
    predict_kwargs = {
        'imgsz': args.imgsz,
        'device': 0,
        'quantize': 16 if args.precision == 'fp16' else 32,
        'verbose': False,
    }

    # 워밍업. 첫 호출은 커널 컴파일·메모리 할당이 섞여 대표성이 없다.
    for i in range(5):
        model.predict(frames[i % len(frames)], **predict_kwargs)
    torch.cuda.synchronize()

    period = 1.0 / args.target_fps
    latencies: list[float] = []
    started = time.perf_counter()
    deadline = started + args.seconds
    next_due = started
    late_count = 0

    while time.perf_counter() < deadline:
        now = time.perf_counter()
        if now < next_due:
            time.sleep(min(next_due - now, 0.005))
            continue

        frame = frames[len(latencies) % len(frames)]
        t0 = time.perf_counter()
        model.predict(frame, **predict_kwargs)
        latencies.append(time.perf_counter() - t0)

        next_due += period
        # 목표 주기를 못 따라가면 밀린 만큼 건너뛴다(누적 폭주 방지).
        if next_due < time.perf_counter():
            late_count += 1
            next_due = time.perf_counter()

    torch.cuda.synchronize()
    elapsed = time.perf_counter() - started

    if not latencies:
        print('추론이 한 번도 실행되지 않았다', file=sys.stderr)
        return 1

    ordered = sorted(latencies)
    result = {
        'model': args.model,
        'precision': args.precision,
        'imgsz': args.imgsz,
        'target_fps': args.target_fps,
        'inferences': len(latencies),
        'elapsed_seconds': round(elapsed, 2),
        'achieved_fps': round(len(latencies) / elapsed, 3),
        'latency_ms': {
            'mean': round(statistics.fmean(latencies) * 1000, 2),
            'p50': round(ordered[len(ordered) // 2] * 1000, 2),
            'p95': round(ordered[int(len(ordered) * 0.95)] * 1000, 2),
            'max': round(ordered[-1] * 1000, 2),
        },
        'late_periods': late_count,
        'note': (
            'TensorRT 미변환 PyTorch 추론이므로 실제 운용보다 CPU 부하가 크다. '
            '이 조건에서 통과하면 하한 보장이다.'
        ),
    }

    text = json.dumps(result, indent=2, ensure_ascii=False)
    print(text)
    if args.out:
        with open(args.out, 'w', encoding='utf-8') as handle:
            handle.write(text + '\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())
