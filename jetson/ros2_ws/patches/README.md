# 외부 패키지 패치 관리

`sentinel.repos`(`vcs import`)로 받아오는 외부 ROS 2 패키지에 적용하는 로컬 패치를 관리한다.

## 규칙

- 외부 패키지 소스는 git에 커밋하지 않는다. root `.gitignore`에 경로를 추가하고 `sentinel.repos`에 URL과 버전을 고정한다.
- 로컬 수정이 필요하면 이 디렉토리에 패치 파일로 두고, 아래 표에 사유·대상 버전·제거 조건을 기록한다.
- 패치는 `vcs import` 직후 적용한다. 대상 패키지 버전을 올릴 때 패치 적용이 실패하면 이 문서를 보고 패치를 갱신하거나 제거한다.

```bash
cd ~/projects/S15P11A301/jetson/ros2_ws
vcs import src < sentinel.repos
git -C src/usb_cam apply "$(pwd)/patches/usb_cam-0.8.1-raw-mjpeg-passthrough.patch"
```

## 패치 목록

### usb_cam-0.8.1-raw-mjpeg-passthrough.patch

| 항목 | 내용 |
|---|---|
| 대상 | ros-drivers/usb_cam `0.8.1` 태그 |
| Jira | S15P11A301-66 (발견·수정), S15P11A301-70 (추적) |
| 업스트림 이슈 | 미제출 — 버전 업그레이드 시 수정 여부를 직접 확인한다 |
| 제거 조건 | 업스트림 릴리스에 수정이 포함되고 `sentinel.repos`의 버전을 해당 릴리스로 올린 뒤 |

**문제**: `pixel_format: raw_mjpeg`(MJPEG 패스스루) 사용 시 노드가 포맷 이름을 `"mjpeg"`와 비교해(실제 이름은 `"raw_mjpeg"`) 패스스루 발행 경로가 전혀 동작하지 않는다. 그 결과 `/camera/image_raw`에 MJPEG 비트스트림이 `yuv422`로 잘못 라벨링되어 발행되고, `/camera/image_raw/compressed`는 image_transport가 그 깨진 데이터를 재인코딩한 결과가 된다. 2026-07-23 기준 업스트림 `main`(0.8.1 이후 31커밋)에서도 미수정 상태를 확인했다.

**수정 내용** (3개 파일, +23/-10줄):

- `src/ros2/usb_cam_node.cpp`: 포맷 이름 비교 두 곳을 `"raw_mjpeg"`로 수정, 압축 메시지를 실제 프레임 크기로 발행
- `src/usb_cam.cpp`, `include/usb_cam/usb_cam.hpp`: V4L2 `bytesused` 추적을 추가해 고정 버퍼 크기(1.84MB) 대신 실제 JPEG 크기(약 105KB)만 복사·발행

**검증**: Jetson Orin Nano + Logitech Brio 100(1280×720@30) 기준 `/camera/image_raw/compressed`가 유효한 카메라 JPEG로 29.5 FPS 발행, `/camera/image_raw` 미발행, 노드 CPU 약 5%.
