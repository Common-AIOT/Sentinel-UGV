# Models

모델 이름, 출처, 라이선스, 입력 크기, 변환 명령, 체크섬과 Jetson 성능 결과를 기록합니다.

가중치(`.pt`, `.onnx`, `.engine` 등)는 Git에 커밋하지 않습니다. 재현 가능한 다운로드 위치 또는 내부 아티팩트 경로와 SHA-256만 관리합니다.

`.gitignore`가 `*.pt`, `*.onnx`, `*.engine`, `*.pth`, `*.weights`를 전역 제외하므로 이 디렉터리에 파일을 두어도 추적되지 않습니다.

## 등록된 가중치

### yolo26n.pt

| 항목 | 내용 |
|---|---|
| 경로 | `jetson/models/yolo26n.pt` (Git 제외) |
| 크기 | 5.3MB |
| SHA-256 | `9b09cc8bf347f0fc8a5f7657480587f25db09b34bf33b0652110fb03a8ad4fef` |
| 용도 | S15P11A301-62 PoC-B 조건 5의 YOLO 부하 주입 (임시) |
| 상태 | COCO 사전학습 baseline. 추가 학습·TensorRT 변환 전 |
| 추론 환경 | `.venv` + `ultralytics 8.4.107`, `torch 2.8.0`(CUDA), `torchvision 0.23.0` |

Jetson 단독 실측(2026-07-27, FP16, Orin Nano 8GB / JetPack 6.2.1+b38):

```text
imgsz=640 : 28.46 FPS (35.1 ms/frame)
imgsz=512 : 28.34 FPS (35.3 ms/frame)
imgsz=416 : 29.37 FPS (34.0 ms/frame)
```

입력 크기를 줄여도 FPS가 거의 변하지 않는다. 연산이 아니라 호출당 오버헤드(Python 전처리·커널 런치)가 병목이므로 TensorRT 전환의 이득이 큰 구간이다.

**FP32는 사용하지 않습니다.** Orin Nano는 CPU와 GPU가 RAM을 공유하며, 여유가 적을 때 FP32 추론은 `CUBLAS_STATUS_ALLOC_FAILED`로 시작조차 못 합니다. 동시 부하에서는 PyTorch 캐싱 할당자의 NVML 조회 경로가 Tegra 미지원으로 assert를 냅니다. 상세는 [`../streaming/poc/README.md`](../streaming/poc/README.md)의 「메모리 제약」을 참조합니다.

### TensorRT 변환 (미수행)

엔진(`.engine`)은 GPU 아키텍처·TensorRT·CUDA 버전에 종속되므로 **반드시 이 Jetson에서 직접 빌드**합니다. 다른 머신에서 만든 엔진은 로드되지 않습니다.

현재 준비 상태:

```text
libnvinfer 10.3.0.30 (+cuda12.5), deb 22개        설치됨
/usr/src/tensorrt/bin/trtexec                     있음
python3 -c "import tensorrt"                      바인딩 없음
프로젝트 .venv: include-system-site-packages      false
```

변환 전에 TensorRT 파이썬 바인딩 설치와 `--system-site-packages` venv가 필요합니다. 변환은 YOLO 배포 티켓(TBD-SW-001 연계)에서 수행하며, 그때 데이터셋 버전·정확도 손실(`yolo val`)·엔진 SHA-256을 이 문서에 함께 기록합니다.

주의: 변환 가이드에 흔히 나오는 16GB 스왑 파일 생성과 `jetson_clocks`는 이 기기에 그대로 적용하지 않습니다. NVMe가 없고 루트 파티션 여유가 약 13GB이며(TBD-VID-001), 클럭을 고정하면 PoC-A·PoC-B 측정과 비교가 불가능해집니다.
