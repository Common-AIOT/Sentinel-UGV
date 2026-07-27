#!/usr/bin/env bash
#
# PoC-B 전체 실행 (S15P11A301-62)
#
# IDE를 닫은 상태에서 이 스크립트 하나만 실행하면 캡처부터 측정까지 끝난다.
# 카메라 점유 순서 때문에 단계가 나뉜다.
#
#   1단계 캡처 : 카메라가 비어 있어야 한다 (usb_cam 미실행)
#   2단계 측정 : usb_cam이 카메라를 점유한 상태여야 한다
#
# 조건 5(YOLO 부하)는 메모리 여유가 필요하다. VS Code 서버 등 큰 프로세스가
# 떠 있으면 PyTorch 할당자가 NVML assert로 죽는다. 시작 전에 여유를 확인하고
# 부족하면 경고한다.
#
# 사용법:
#   ./poc_b_run_all.sh [측정초] [출력디렉터리]
#
# 주의: ROS setup.bash는 unset 변수를 참조하므로 set -u를 쓰지 않는다.
set -Eeo pipefail

DURATION="${1:-600}"
OUT_DIR="${2:-$HOME/poc_b_results}"
CAPTURE_SECONDS="${CAPTURE_SECONDS:-30}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WS="${WS:-$HOME/projects/S15P11A301/jetson/ros2_ws}"
MIN_FREE_MB="${MIN_FREE_MB:-1500}"

mkdir -p "${OUT_DIR}"
SUMMARY="${OUT_DIR}/SUMMARY.txt"

log() { echo "$@" | tee -a "${SUMMARY}"; }

: > "${SUMMARY}"
log "=============================================="
log " PoC-B 전체 실행  $(date '+%Y-%m-%d %H:%M:%S')"
log " 측정 시간: ${DURATION}초 / 출력: ${OUT_DIR}"
log "=============================================="
log ""

# ---------- 0. 환경 점검 ----------
log "--- 0. 환경 점검 ---"
free_mb=$(free -m | awk '/Mem:/{print $4}')
avail_mb=$(free -m | awk '/Mem:/{print $7}')
log "메모리: free ${free_mb}MB / available ${avail_mb}MB / total $(free -m | awk '/Mem:/{print $2}')MB"

big=$(ps -eo rss,comm --sort=-rss | awk 'NR>1 && $1>300000 {printf "%s(%.1fGB) ", $2, $1/1048576}')
if [ -n "${big}" ]; then
  log "300MB 이상 프로세스: ${big}"
fi

if [ "${avail_mb}" -lt "${MIN_FREE_MB}" ]; then
  log "경고: available 메모리가 ${MIN_FREE_MB}MB 미만이다."
  log "      YOLO 부하가 NVML assert로 죽을 수 있다."
  log "      VS Code 서버 등을 종료하고 다시 실행하는 것을 권한다."
  log "      그래도 계속하려면 10초 안에 Ctrl+C를 누르지 않으면 진행한다."
  sleep 10
fi

if [ ! -x "$HOME/projects/S15P11A301/.venv/bin/python" ]; then
  log "경고: .venv python이 없다. YOLO 부하 없이 진행한다(조건 5 부분 측정)."
fi
log ""

# ---------- 1. MJPEG 샘플 캡처 ----------
log "--- 1. MJPEG 샘플 캡처 (${CAPTURE_SECONDS}초) ---"
if pgrep -f usb_cam_node_exe >/dev/null 2>&1; then
  log "usb_cam이 실행 중이다. 종료하고 캡처한다."
  pkill -f usb_cam_node_exe || true
  pkill -f "sensors.launch.py" || true
  sleep 3
fi

log ""
log ">>> 지금부터 ${CAPTURE_SECONDS}초간 카메라 영상을 파일로 담는다."
log ">>> 조건 1을 조건 4와 같은 기준으로 재려면 이 구간이 고복잡도여야 한다."
log ">>> 카메라 앞에서 움직이거나, 질감이 많은 물체를 비춰라."
log ""
for i in 5 4 3 2 1; do printf "  %d초 후 시작...\r" "${i}"; sleep 1; done
echo

"${HERE}/poc_b_capture.sh" "${CAPTURE_SECONDS}" "${OUT_DIR}" 2>&1 | tee -a "${SUMMARY}"

sample_bytes=$(stat -c%s "${OUT_DIR}/camera_sample.mjpeg")
sample_frames=$((CAPTURE_SECONDS * 30))
log "샘플 프레임 평균: $((sample_bytes / sample_frames)) bytes"
log "  (30KB 근처면 저복잡도, 90KB 이상이면 고복잡도)"
log ""

# ---------- 2. 센서 기동 ----------
log "--- 2. sensors.launch.py 기동 ---"
source /opt/ros/humble/setup.bash
source "${WS}/install/setup.bash"

nohup ros2 launch sentinel_bringup sensors.launch.py \
  > "${OUT_DIR}/sensors.log" 2>&1 &
sensors_pid=$!
log "launch PID ${sensors_pid}, 토픽 대기..."

for _ in $(seq 1 30); do
  if timeout 5 ros2 topic list 2>/dev/null | grep -q '/camera/image_raw/compressed'; then
    break
  fi
  sleep 2
done

if ! timeout 5 ros2 topic list 2>/dev/null | grep -q '/camera/image_raw/compressed'; then
  log "실패: 압축 토픽이 올라오지 않았다. ${OUT_DIR}/sensors.log 확인"
  kill "${sensors_pid}" 2>/dev/null || true
  exit 1
fi
log "노드: $(timeout 5 ros2 node list 2>/dev/null | tr '\n' ' ')"
log ""

# ---------- 3. 풀부하 측정 ----------
log "--- 3. 풀부하 측정 (${DURATION}초) ---"
log "시작 $(date '+%H:%M:%S'), 예상 종료 $(date -d "+${DURATION} seconds" '+%H:%M:%S')"
log ""

"${HERE}/poc_b_fullload.sh" "${DURATION}" "${OUT_DIR}" 2>&1 | tee -a "${SUMMARY}"

# ---------- 4. 정리 ----------
log ""
log "--- 4. 정리 ---"
pkill -f "sensors.launch.py" 2>/dev/null || true
pkill -f usb_cam_node_exe 2>/dev/null || true
pkill -f ydlidar_ros2_driver_node 2>/dev/null || true
sleep 2
log "센서 종료"
log "종료 시 메모리: available $(free -m | awk '/Mem:/{print $7}')MB"
log ""
log "=============================================="
log " 완료. 결과 위치: ${OUT_DIR}"
log ""
log " 공유할 파일:"
log "   ${SUMMARY}                (요약 — 이거만 보내도 됨)"
log "   ${OUT_DIR}/yolo_load.json  (조건 5)"
log "   ${OUT_DIR}/dds_*.json      (조건 2·4)"
log "   ${OUT_DIR}/resources.txt   (자원 추이)"
log "=============================================="
