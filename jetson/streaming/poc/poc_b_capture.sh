#!/usr/bin/env bash
#
# PoC-B 0/3 — MJPEG 샘플 캡처 (S15P11A301-62)
#
# 카메라를 단독 점유해 실제 Brio MJPEG 비트스트림을 파일로 남긴다.
# 이후 디코딩·인코딩 측정은 이 파일을 반복 재생해서 수행한다.
#
# 왜 파일로 두는가:
#   usb_cam이 카메라를 단독 점유하는 것이 확정 계약이라 /dev/video0을 두 번
#   열 수 없다. 디코더 2인스턴스 동시 부하를 재려면 소스가 파일이어야 한다.
#   같은 카메라의 실제 MJPEG 데이터이므로 디코딩 부하는 동등하다.
#
# 전제: sensors.launch.py를 내려서 카메라가 비어 있어야 한다.
#
# 사용법:
#   ./poc_b_capture.sh [캡처초] [출력디렉터리]
set -Eeo pipefail

DURATION="${1:-30}"
OUT_DIR="${2:-/tmp/poc_b}"
DEVICE="${DEVICE:-/dev/video0}"
WIDTH="${WIDTH:-1280}"
HEIGHT="${HEIGHT:-720}"
FPS="${FPS:-30}"

mkdir -p "${OUT_DIR}"
SAMPLE="${OUT_DIR}/camera_sample.mjpeg"

if fuser "${DEVICE}" >/dev/null 2>&1; then
  echo "${DEVICE}를 다른 프로세스가 점유 중이다:" >&2
  fuser -v "${DEVICE}" >&2 || true

  # usb_cam은 launch가 관리하므로 여기서 손대지 않고 중단한다.
  if pgrep -f usb_cam_node_exe >/dev/null 2>&1; then
    echo "usb_cam이 점유 중이다. sensors.launch.py를 내린 뒤 다시 실행한다." >&2
    exit 1
  fi

  # 이전 캡처가 남긴 gst-launch 등 잔여 프로세스는 정리하고 계속한다.
  # 정리하지 않으면 v4l2src가 장치를 열지 못해 샘플이 비게 된다.
  echo "잔여 프로세스를 정리하고 계속한다." >&2
  fuser -k "${DEVICE}" >/dev/null 2>&1 || true
  sleep 2

  if fuser "${DEVICE}" >/dev/null 2>&1; then
    echo "정리 후에도 ${DEVICE}가 점유돼 있다. 수동 확인이 필요하다." >&2
    fuser -v "${DEVICE}" >&2 || true
    exit 1
  fi
  echo "정리 완료." >&2
fi

echo "MJPEG 샘플 캡처: ${WIDTH}x${HEIGHT}@${FPS}, ${DURATION}초 -> ${SAMPLE}"
echo "장면을 고정하지 말고 실제 운용에 가까운 화면을 담는다(JPEG 크기 변동 반영)."

timeout "${DURATION}" gst-launch-1.0 -q \
  v4l2src device="${DEVICE}" io-mode=mmap \
  ! "image/jpeg,width=${WIDTH},height=${HEIGHT},framerate=${FPS}/1" \
  ! filesink location="${SAMPLE}" || true

if [[ ! -s "${SAMPLE}" ]]; then
  echo "캡처 실패: ${SAMPLE}가 비어 있다." >&2
  exit 1
fi

bytes=$(stat -c%s "${SAMPLE}")
frames=$(( DURATION * FPS ))
echo
echo "캡처 완료: $(numfmt --to=iec "${bytes}") (${DURATION}초 x ${FPS}fps 기준 약 ${frames}프레임)"
echo "프레임 평균 약 $(( bytes / frames )) bytes"
