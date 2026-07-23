<!--
  GENERATED FILE - DO NOT EDIT DIRECTLY.
  Source: docs/specifications/Sentinel_UGV_최종_통합_명세서_v1.0-rc1.md
  Generator: scripts/docs/split-integrated-spec.ps1
-->

> 기준 문서: Sentinel UGV 통합 명세서 v1.0-rc1 (2026-07-22). 변경은 전체본에 반영한 뒤 분할 스크립트를 실행합니다.

# 부록 K. 소프트웨어 기준선

| 항목 | 기준 | 확인 상태·명령 |
|---|---|---|
| JetPack | `6.2.1+b38` | 실기기 확인 완료 |
| Jetson Linux | 36.4.4 계열 | `cat /etc/nv_tegra_release` |
| Ubuntu | 22.04 | `lsb_release -a` |
| ROS 2 | Humble | `printenv ROS_DISTRO` |
| CUDA | 실기기 값 기록 | `nvcc --version` 또는 `dpkg-query` |
| TensorRT | 실기기 값 기록 | `dpkg-query -W '*nvinfer*'` |
| PyTorch·Ultralytics | lockfile·이미지 태그 기록 | `pip freeze` |
| YOLO | `yolo26n.pt` 또는 변환 engine의 SHA-256 | 모델 파일 해시 |
| STM32 firmware | Git commit·protocol version | HELLO_ACK·release manifest |
| Backend·Frontend | Git tag·Docker digest | release manifest |
| PostgreSQL·TimescaleDB·MediaMTX | Docker image digest | `docker inspect` |

버전 기준선 변경은 전체 인수 시험 재실행 없이 시연 직전에 수행하지 않는다.
