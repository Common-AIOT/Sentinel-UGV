# 제3자 구성요소와 라이선스

이 저장소가 쓰는 외부 구성요소와 그 라이선스를 적는다. **확인한 것과 확인이 필요한 것을
섞지 않는다** — 확인란이 비어 있으면 그것은 「문제없음」이 아니라 「아직 안 봤음」이다.

작성 기준일 2026-08-13 (S15P11A301-382).

## 프로젝트 자체 라이선스는 아직 정하지 않았다

루트에 `LICENSE` 파일이 없다. **이 문서는 그것을 정하지 않는다** — 팀과 SSAFY의 결정
사항이다. 다만 결정에 제약이 있다는 사실은 여기 남긴다.

`ultralytics` 가 AGPL-3.0 이므로, 이 저장소를 공개하거나 배포할 때 선택지가 좁아진다.
AGPL 은 **네트워크 너머로 서비스를 제공하는 경우까지** 소스 공개 의무를 건다. 이 프로젝트는
관제 웹이 탐지 결과를 제공하므로 그 경계에 실제로 닿는다. 결정 전에 이 문단을 먼저 읽는다.

## 확인한 것

출처를 함께 적는다. 다음 사람이 재확인할 수 있어야 한다.

| 구성요소 | 쓰는 곳 | 라이선스 | 출처 |
|---|---|---|---|
| `ultralytics` (YOLO26n Detect·Pose) | `ai/detection` | **AGPL-3.0 이상** 또는 Ultralytics Enterprise License | [PyPI 패키지 라이선스 필드](https://pypi.org/project/ultralytics/) |
| MinIO 서버 | `backend/compose.*.yaml` (S3 호환 스토리지) | **AGPL v3** (클라이언트 SDK 는 Apache 2.0) | [min.io 라이선스 공지](https://www.min.io/blog/from-open-source-to-free-and-open-source-minio-is-now-fully-licensed-under-gnu-agplv3) · [minio/minio LICENSE](https://github.com/minio/minio/blob/master/LICENSE) |
| TimescaleDB | 관제 DB | Apache 2.0(오픈소스판) / TSL(커뮤니티판) | [Tiger Data 라이선스 문서](https://www.tigerdata.com/legal/licenses) |
| Asta Sans, D2Coding (서브셋 woff2) | `frontend/public/fonts` | SIL Open Font License 1.1 — **고지 의무 있음** | [frontend/ATTRIBUTIONS.md](frontend/ATTRIBUTIONS.md) |
| shadcn/ui 컴포넌트 | `frontend` | MIT | [frontend/ATTRIBUTIONS.md](frontend/ATTRIBUTIONS.md) |

TimescaleDB 는 **DBaaS 로 팔지 않는 한** 커뮤니티 기능을 무료로 쓸 수 있다. 현재 사용
형태(자체 운영)에는 제약이 없다. 기록만 남긴다.

## 확인이 필요한 것

아래는 **아직 확인하지 않았다.** 저장소를 공개하거나 배포하기 전에 채운다.

| 구성요소 | 쓰는 곳 |
|---|---|
| ROS 2 Humble, Nav2, SLAM Toolbox, robot_localization | `jetson/ros2_ws` |
| `usb_cam`, `ydlidar_ros2_driver` (벤더 드라이버, Git 제외·`sentinel.repos` 로 가져옴) | `jetson/ros2_ws/src` |
| Silero VAD | `ai/voice` |
| Qwen3-ASR-1.7B (모델 가중치) | 원격 ASR 서버 |
| DeepFilterNet | `ai/voice/denoise` |
| PyTorch, torchvision, torchaudio, OpenCV | `ai/detection`, `ai/voice` |
| MediaMTX | Jetson 스트리밍 |
| Eclipse Mosquitto | 관제 MQTT |
| Spring Boot, Next.js, React, Tailwind CSS | `backend`, `frontend` |

모델 가중치는 라이브러리와 라이선스가 다를 수 있다. `yolo26n.pt`·`yolo26n-pose.pt` 는
Git 에 넣지 않고 릴리스 묶음에서 해시와 함께 관리한다(TBD-SW-001).

## 함께 알아 둘 것 — MinIO 프로젝트 상태

라이선스와 별개의 의존성 리스크다. MinIO 는 2025-12 유지보수 모드를 거쳐 2026 초
아카이브됐고, 커뮤니티 에디션은 사전 컴파일 바이너리 없이 소스로만 배포된다.

현재 사용처는 로컬 개발 compose 와 S3 호환 스토리지이고 백엔드는 S3 API 로만 붙으므로
(`S3_ENDPOINT` 교체로 다른 구현으로 바꿀 수 있다) 갇힌 구조는 아니다. 인수인계 시 알고
있어야 할 값이다.

## 커밋하지 않는 것

라이선스와 별개로 저장소에 넣지 않는 것들이다(루트 README 「안전 원칙」과 같은 규칙).

- `.env`, 인증서, SSH 키, 클라우드 자격 증명
- 모델 가중치·TensorRT 엔진
- 개인 음성(요구조자·팀원 녹음)
