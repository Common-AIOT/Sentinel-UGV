#!/usr/bin/env bash
#
# PoC-B 3/3 — 풀부하 헤드룸 측정 (S15P11A301-62)
#
# 동시에 걸리는 부하:
#   1. usb_cam (카메라 단독 점유, 압축 토픽 발행)      <- sensors.launch.py
#   2. ydlidar (/scan)                                 <- sensors.launch.py
#   3. nvv4l2decoder 인스턴스 A (AI 브랜치 상당, 디코딩만)
#   4. nvv4l2decoder 인스턴스 B + x264enc (스트리밍 브랜치)
#   5. 압축 토픽 구독자 2개 (DDS 전송 경로 측정)
#   6. YOLO26n Detect 추론 15 FPS (조건 5의 "YOLO 몫" 부하)
#      YOLO_VENV가 없거나 모델이 없으면 이 부하를 건너뛰고 그 사실을 출력한다.
#
# 측정 항목: 인코딩 FPS, 코어별 CPU, NVJPG 엔진 상태, 구독자별 드롭,
#            JPEG 크기 분포, 메모리·온도.
#
# 알려진 한계 (숨기지 않고 기록한다):
#   디코더 입력이 ROS 압축 토픽이 아니라 MJPEG 샘플 파일이다. ROS -> appsrc
#   브릿지는 S15P11A301-106 구현 범위이므로 PoC 단계에서는 만들지 않는다.
#   따라서 이 측정은 디코딩·인코딩 연산 부하와 DDS 전송 부하를 각각 재고,
#   둘 사이의 핸드오프 비용(appsrc 복사)은 포함하지 않는다.
#
# 전제:
#   1. ./poc_b_capture.sh 로 MJPEG 샘플을 미리 만들어 둔다.
#   2. 그 후 sensors.launch.py를 띄운 상태에서 이 스크립트를 실행한다.
#
# 사용법:
#   ./poc_b_fullload.sh [측정초] [출력디렉터리]
set -Eeo pipefail

DURATION="${1:-90}"
OUT_DIR="${2:-/tmp/poc_b}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WS="${WS:-/home/orin/projects/S15P11A301/jetson/ros2_ws}"
SAMPLE="${OUT_DIR}/camera_sample.mjpeg"

mkdir -p "${OUT_DIR}"

# ROS setup.bash는 unset 변수를 참조하므로 set -u를 쓰지 않는다.
source /opt/ros/humble/setup.bash
source "${WS}/install/setup.bash"

if [[ ! -s "${SAMPLE}" ]]; then
  echo "MJPEG 샘플이 없다: ${SAMPLE}" >&2
  echo "sensors.launch.py를 내리고 ./poc_b_capture.sh 를 먼저 실행한다." >&2
  exit 1
fi

if ! timeout 10 ros2 topic list 2>/dev/null | grep -q '/camera/image_raw/compressed'; then
  echo "/camera/image_raw/compressed가 없다. sensors.launch.py를 먼저 띄운다." >&2
  exit 1
fi

echo "PoC-B 풀부하 측정 ${DURATION}초 시작"
echo "  샘플: ${SAMPLE}"
echo "  출력: ${OUT_DIR}"
echo

pids=()

# --- 3. AI 브랜치 상당: 디코딩만 하는 독립 인스턴스 ---
GST_DEBUG=fpsdisplaysink:5 timeout "${DURATION}" gst-launch-1.0 -q \
  multifilesrc location="${SAMPLE}" loop=true \
  ! "image/jpeg,framerate=30/1" \
  ! jpegparse ! nvv4l2decoder mjpeg=1 \
  ! nvvidconv ! "video/x-raw,format=I420" \
  ! fpsdisplaysink video-sink=fakesink text-overlay=false sync=true \
  > "${OUT_DIR}/load_decoder_ai.log" 2>&1 &
pids+=($!)

# --- 4. 스트리밍 브랜치: 디코딩 + x264 인코딩 ---
LABEL=fullload OUT_DIR="${OUT_DIR}" \
  timeout "$((DURATION + 30))" "${HERE}/poc_b_encode.sh" "${DURATION}" "${OUT_DIR}" "${SAMPLE}" \
  > "${OUT_DIR}/load_encode.log" 2>&1 &
pids+=($!)

# --- 5. 압축 토픽 구독자 2개 ---
timeout "$((DURATION + 20))" python3 "${HERE}/poc_b_dds.py" \
  --label streaming --seconds "${DURATION}" --out "${OUT_DIR}/dds_streaming.json" \
  > "${OUT_DIR}/dds_streaming.log" 2>&1 &
pids+=($!)

timeout "$((DURATION + 20))" python3 "${HERE}/poc_b_dds.py" \
  --label ai --seconds "${DURATION}" --out "${OUT_DIR}/dds_ai.json" \
  > "${OUT_DIR}/dds_ai.log" 2>&1 &
pids+=($!)

# --- 6. YOLO 추론 부하 (조건 5) ---
YOLO_VENV="${YOLO_VENV:-/home/orin/projects/S15P11A301/.venv/bin/python}"
YOLO_MODEL="${YOLO_MODEL:-/home/orin/projects/S15P11A301/jetson/models/yolo26n.pt}"
YOLO_FPS="${YOLO_FPS:-15}"
yolo_active=0
if [[ -x "${YOLO_VENV}" && -s "${YOLO_MODEL}" ]]; then
  yolo_active=1
  timeout "$((DURATION + 40))" "${YOLO_VENV}" "${HERE}/poc_b_yolo_load.py" \
    --model "${YOLO_MODEL}" --seconds "${DURATION}" --target-fps "${YOLO_FPS}" \
    --out "${OUT_DIR}/yolo_load.json" \
    > "${OUT_DIR}/yolo_load.log" 2>&1 &
  pids+=($!)
  echo "  YOLO 부하: ${YOLO_MODEL} @ ${YOLO_FPS} FPS (FP16)"
else
  echo "  YOLO 부하: 건너뜀 (venv 또는 모델 없음) — 조건 5는 부분 측정에 그친다" >&2
fi

# --- 자원 샘플링 ---
: > "${OUT_DIR}/resources.txt"
(
  end=$((SECONDS + DURATION))
  while [ "${SECONDS}" -lt "${end}" ]; do
    {
      echo "--- $(date '+%H:%M:%S') ---"
      # 코어별 CPU 사용률과 클럭
      grep -E '^cpu[0-9]' /proc/stat | awk '{print $1, "jiffies:", $2+$3+$4}' | tr '\n' ' '
      echo
      echo "loadavg: $(cut -d' ' -f1-3 /proc/loadavg)"
      echo "mem    : $(free -m | awk '/Mem:/{print $3"MB used / "$2"MB"}')"
      echo "temp   : $(cat /sys/devices/virtual/thermal/thermal_zone*/temp 2>/dev/null | sort -n | tail -1)"
      for engine in /sys/devices/platform/bus@0/*nvjpg*/power/runtime_status \
                    /sys/kernel/debug/bpmp/debug/clk/nvjpg/rate; do
        [ -r "${engine}" ] && echo "nvjpg  : ${engine##*/} = $(cat "${engine}" 2>/dev/null)"
      done
    } >> "${OUT_DIR}/resources.txt"
    sleep 5
  done
) &
pids+=($!)

echo "측정 중... (${DURATION}초)"
for pid in "${pids[@]}"; do
  wait "${pid}" 2>/dev/null || true
done

echo
echo "================ PoC-B 결과 요약 ================"
echo
echo "--- 합격 조건 1·3: x264 인코딩 ---"
sed -n '/FPS (합격 조건 1/,$p' "${OUT_DIR}/load_encode.log" 2>/dev/null | head -22 \
  || echo "인코딩 결과 없음 — ${OUT_DIR}/load_encode.log 확인"

echo
echo "--- AI 브랜치 디코더 (독립 2번째 인스턴스) ---"
if grep -q "min-fps to" "${OUT_DIR}/load_decoder_ai.log" 2>/dev/null; then
  grep -oE "min-fps to [0-9.]+" "${OUT_DIR}/load_decoder_ai.log" | tail -1 \
    | sed 's/min-fps to /최저 순간 FPS: /'
else
  echo "디코더 FPS 로그 없음 — ${OUT_DIR}/load_decoder_ai.log 확인"
fi

echo
echo "--- 합격 조건 2·4: DDS 전송 ---"
dds_results=()
for f in "${OUT_DIR}/dds_streaming.json" "${OUT_DIR}/dds_ai.json"; do
  if [[ -s "${f}" ]]; then
    dds_results+=("${f}")
  else
    echo "$(basename "${f}"): 결과 없음"
  fi
done
if [[ ${#dds_results[@]} -gt 0 ]]; then
  python3 "${HERE}/poc_b_dds.py" --summarize "${dds_results[@]}" || true
fi

echo
echo "--- 조건 5 부하: YOLO 추론 ---"
if [[ "${yolo_active}" -eq 1 && -s "${OUT_DIR}/yolo_load.json" ]]; then
  python3 -c "
import json
d = json.load(open('${OUT_DIR}/yolo_load.json'))
lat = d['latency_ms']
print(f\"  {d['precision']} imgsz={d['imgsz']}: 목표 {d['target_fps']} -> 실측 {d['achieved_fps']} FPS\")
print(f\"  지연 평균 {lat['mean']}ms / p95 {lat['p95']}ms / 최대 {lat['max']}ms, 밀림 {d['late_periods']}회\")
"
  echo "  주의: TensorRT 미변환 PyTorch 추론이라 실제보다 부하가 크다 (하한 보장)"
else
  echo "  YOLO 부하 미적용 — 조건 5는 부분 측정이다"
fi

echo
echo "--- 합격 조건 5: CPU 헤드룸 ---"
awk '/loadavg:/ {n++; split($2,a," "); print}' "${OUT_DIR}/resources.txt" 2>/dev/null \
  | sort -k2 -n | tail -1 | sed 's/loadavg:/최대 loadavg(1분):/'
grep "mem    :" "${OUT_DIR}/resources.txt" 2>/dev/null | tail -1
grep "temp   :" "${OUT_DIR}/resources.txt" 2>/dev/null | tail -1
echo "코어 수: $(nproc) — loadavg를 코어 수로 나눠 점유율을 본다"
if [[ "${yolo_active}" -ne 1 ]]; then
  echo "주의: YOLO 부하가 걸리지 않았으므로 여기서 나온 헤드룸은 상한이다."
fi

echo
echo "판정은 jetson/streaming_poc/README.md의 PoC-B 합격 조건 6개와 대조한다."
echo "조건 6(링 버퍼 쓰기 지연)은 별도 측정이다 — 링 writer 구현 후."
