# PoC-B 측정 스크립트

S15P11A301-62의 PoC-B(x264 인코딩 성능 + 풀부하 헤드룸) 실측 도구다. 합격 조건과 판정 기준은 [`../README.md`](../README.md)의 PoC-B 절이 규범이고, 이 디렉터리는 그 조건을 재현 가능하게 측정하는 수단만 담는다.

PoC-A는 측정 스크립트를 남기지 않아 재현이 불가능했다. PoC-B는 항목이 훨씬 많으므로 스크립트를 저장소에 두고 결과를 그 스크립트로만 만든다.

## 실행 순서

```bash
cd jetson/streaming_poc/poc

# 0. MJPEG 샘플 캡처 — 카메라가 비어 있어야 한다(sensors.launch.py 내린 상태)
./poc_b_capture.sh 30

# 1. sensors.launch.py를 띄운다 (다른 터미널)
#    ros2 launch sentinel_bringup sensors.launch.py

# 2. 풀부하 측정
./poc_b_fullload.sh 90
```

개별 실행도 가능하다.

| 스크립트 | 측정 대상 | 합격 조건 |
|---|---|---|
| `poc_b_capture.sh` | (준비) 실제 Brio MJPEG 비트스트림 캡처 | — |
| `poc_b_encode.sh` | x264 인코딩 FPS, H.264 스트림 속성 | 1, 3 |
| `poc_b_dds.py` | 압축 토픽 수신율·드롭·JPEG 크기 분포 | 2, 4 |
| `poc_b_fullload.sh` | 위 전부 + 디코더 2인스턴스 동시 + 자원 샘플링 | 1~5 |

합격 조건 6(링 버퍼 쓰기 지연으로 인한 조각 누락)은 링 writer가 없어 측정할 수 없다. S15P11A301-106에서 writer 구현 후 별도로 측정한다.

## 왜 디코더 입력이 파일인가

`usb_cam`이 카메라를 단독 점유하는 것이 확정 계약이므로 `/dev/video0`을 두 번 열 수 없다. 디코더 2인스턴스 동시 부하를 재려면 소스가 파일이어야 한다. 같은 카메라에서 캡처한 실제 MJPEG 비트스트림이라 디코딩 연산 부하는 동등하다.

**이 방식이 포함하지 않는 것**: ROS 압축 토픽에서 GStreamer `appsrc`로 넘기는 핸드오프 비용(메시지 복사). 그 브릿지는 S15P11A301-106 구현 범위다. 따라서 PoC-B는 디코딩·인코딩 연산 부하와 DDS 전송 부하를 **각각** 재고, 둘 사이의 접합 비용은 재지 않는다. 이 한계를 결과 기록에 함께 남긴다.

## 드롭 판정 방법

`sensor_msgs/CompressedImage`에는 시퀀스 번호가 없다. 그래서 `header.stamp` 간격으로 유실을 추정한다. 카메라가 V4L2 캡처 시각을 stamp에 넣으므로(62 계약) 프레임이 유실되면 연속 stamp 간격이 프레임 주기의 배수로 벌어진다.

```text
프레임 주기 33.3ms 기준, 간격이 50ms(1.5배)를 넘으면 그 사이 유실로 계산
유실 프레임 수 = round(간격 / 주기) - 1
```

`poc_b_dds.py`는 구독 전에 발행자 QoS를 조회해 맞춘다. QoS가 어긋나면 연결 자체가 되지 않거나 의도치 않은 드롭이 생겨 측정이 무의미해진다. 수신이 0이면 먼저 QoS 불일치를 의심한다.

## JPEG 크기와 DDS 단편화

Fast DDS 기본 UDP 페이로드 한계(약 64KB)를 넘는 메시지는 단편화된다. 지금까지 관측된 JPEG 크기는 장면에 따라 크게 다르다.

```text
정적 실내 저복잡도 장면 : 평균 약 30KB (2026-07-27 측정)
S15P11A301-66 당시 측정 : 평균 약 105KB
```

64KB 임계가 이 두 값 사이에 있다. 즉 장면 복잡도에 따라 단편화 여부가 갈리므로 **평균이 아니라 최대값**으로 판정해야 한다. `poc_b_dds.py`가 `over_64kb_count`를 함께 보고한다. 측정 시 장면을 고정하지 말고 실제 운용에 가까운 화면을 담는다.

## YOLO 부하 주입과 메모리 제약

조건 5는 실제 추론 부하를 걸어야 재진다. `poc_b_yolo_load.py`가 `jetson/models/yolo26n.pt`를 FP16으로 목표 FPS에 맞춰 돌린다.

```bash
~/projects/S15P11A301/.venv/bin/python poc_b_yolo_load.py --seconds 600 --target-fps 15
```

**TensorRT 미변환 PyTorch 추론이다.** TensorRT보다 CPU 전처리·커널 런치 오버헤드가 크므로 실제 운용보다 비관적인 부하다. 이 조건에서 스트리밍이 목표 FPS를 지키면 상한이 아니라 **하한 보장**이다. 미달하면 TensorRT 변환 후 재측정해야 하고 조건 5는 미확정으로 남는다.

단독 실측(2026-07-27, FP16):

```text
imgsz=640 : 28.46 FPS (35.1 ms/frame)
imgsz=512 : 28.34 FPS (35.3 ms/frame)
imgsz=416 : 29.37 FPS (34.0 ms/frame)
```

입력 크기를 줄여도 FPS가 거의 변하지 않는다. 연산이 아니라 호출당 오버헤드가 병목이라는 뜻이고, TensorRT 전환의 이득이 큰 지점이다.

### 메모리 제약 — 실측으로 드러난 제약

Orin Nano 8GB는 CPU와 GPU가 같은 RAM 풀을 쓴다. 여유가 적으면 두 가지 형태로 실패한다.

```text
FP32 추론 시작 시 : CUDA error: CUBLAS_STATUS_ALLOC_FAILED when calling cublasCreate
동시 부하 중      : NVML_SUCCESS == r INTERNAL ASSERT FAILED
                    at c10/cuda/CUDACachingAllocator.cpp:1131
```

둘 다 뿌리는 같다. PyTorch 캐싱 할당자가 메모리 압박에서 재시도할 때 NVML로 가용량을 조회하는데, **Tegra 통합 GPU는 NVML을 지원하지 않아** 조회 자체가 assert로 터진다. 즉 NVML 오류는 원인이 아니라 메모리 부족의 증상이다.

대응 순서:

1. FP32를 쓰지 않는다(FP16 고정). FP32는 여유가 적을 때 시작조차 못 한다.
2. 측정 중 다른 큰 프로세스를 띄우지 않는다.
3. 그래도 재발하면 `imgsz`를 512/416으로 낮춘다(명세 25.5가 허용).

**측정 환경 주의**: 2026-07-27 측정 시점에 VS Code 서버와 확장이 약 2.7GB를 점유하고 있었다. 실제 로봇 운용에는 없는 부하다. 따라서 이 환경의 메모리 여유는 운용 환경보다 나쁘고, 여기서 메모리로 실패한 것이 운용에서도 실패한다는 뜻은 아니다. 반대로 여기서 통과하면 운용에서도 통과한다. S15P11A301-69에서 기록한 page-cache OOM과 같은 제약군이다.

## 스크립트 작성 시 주의

ROS 2의 `setup.bash`는 unset 변수를 참조하므로 `set -u`와 함께 쓸 수 없다. `AMENT_TRACE_SETUP_FILES: unbound variable`로 즉시 죽는다. 이 디렉터리의 스크립트는 `set -Eeo pipefail`만 쓴다.

`ros2 topic hz`의 출력을 `head`/`tail`로 파이프한 뒤 `timeout`으로 죽이면 버퍼가 flush되지 않아 결과가 사라진다. 파일로 리다이렉트한 뒤 읽는다.
