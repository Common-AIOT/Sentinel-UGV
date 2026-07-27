#!/usr/bin/env bash
#
# PoC-B 1/3 — x264 인코딩 성능 측정 (S15P11A301-62)
#
# MJPEG -> jpegparse -> nvv4l2decoder(mjpeg=1) -> nvvidconv -> I420
#       -> x264enc -> h264parse -> (filesink + fpsdisplaysink)
#
# 합격 조건 1·3: 실측 FPS >= 29, H.264 profile=baseline / bframes=0 / GOP 30
#
# 소스는 두 가지를 받는다.
#   기본       : MJPEG 샘플 파일 (poc_b_capture.sh로 만든다)
#   SOURCE=cam : /dev/video0 직접
# usb_cam이 카메라를 단독 점유하므로, sensors.launch.py가 떠 있는 상태에서는
# 반드시 파일 소스를 사용한다. 카메라를 두 번 열 수 없다.
#
# 사용법:
#   ./poc_b_encode.sh [측정초] [출력디렉터리] [MJPEG샘플경로]
#
# 주의: ROS setup.bash는 unset 변수를 참조하므로 이 스크립트에서 set -u를 쓰지 않는다.
set -Eeo pipefail

DURATION="${1:-60}"
OUT_DIR="${2:-/tmp/poc_b}"
SAMPLE_IN="${3:-${OUT_DIR}/camera_sample.mjpeg}"

SOURCE="${SOURCE:-file}"
DEVICE="${DEVICE:-/dev/video0}"
WIDTH="${WIDTH:-1280}"
HEIGHT="${HEIGHT:-720}"
FPS="${FPS:-30}"
BITRATE_KBPS="${BITRATE_KBPS:-2500}"
LABEL="${LABEL:-encode}"

mkdir -p "${OUT_DIR}"
H264_OUT="${OUT_DIR}/poc_b_${LABEL}.h264"
FPS_LOG="${OUT_DIR}/poc_b_${LABEL}_fps.log"
PROBE_LOG="${OUT_DIR}/poc_b_${LABEL}_ffprobe.log"

> "${FPS_LOG}"

if [[ "${SOURCE}" == "cam" ]]; then
  src_desc="v4l2src ${DEVICE}"
  read -r -a SRC_ELEMS <<< "v4l2src device=${DEVICE} io-mode=mmap"
  CAPS="image/jpeg,width=${WIDTH},height=${HEIGHT},framerate=${FPS}/1"
  # 카메라는 하드웨어가 30fps로 페이싱한다.
  SINK_SYNC="${SINK_SYNC:-false}"
else
  if [[ ! -s "${SAMPLE_IN}" ]]; then
    echo "MJPEG 샘플이 없다: ${SAMPLE_IN}" >&2
    echo "먼저 ./poc_b_capture.sh 를 실행한다(카메라가 비어 있어야 함)." >&2
    exit 1
  fi
  src_desc="filesrc ${SAMPLE_IN} (loop)"
  # multifilesrc loop=true로 샘플을 반복 재생해 측정 시간을 채운다.
  read -r -a SRC_ELEMS <<< "multifilesrc location=${SAMPLE_IN} loop=true"
  CAPS="image/jpeg,framerate=${FPS}/1"
  # 파일 소스는 페이싱이 없다. sync=true로 30fps 실시간에 맞춰야 CPU 사용률이
  # 실제 운용과 같아진다. sync=false면 최대 처리량만 재고 헤드룸이 왜곡된다.
  SINK_SYNC="${SINK_SYNC:-true}"
fi

echo "PoC-B 인코딩 측정"
echo "  소스     : ${src_desc}"
echo "  해상도   : ${WIDTH}x${HEIGHT} @ ${FPS}"
echo "  비트레이트: ${BITRATE_KBPS} kbps"
echo "  측정시간 : ${DURATION}초"
echo "  sink sync: ${SINK_SYNC} (true = 30fps 실시간 페이싱)"
echo "  출력     : ${OUT_DIR}"
echo

started=${SECONDS}
GST_DEBUG=fpsdisplaysink:5 timeout "${DURATION}" gst-launch-1.0 -q \
  "${SRC_ELEMS[@]}" \
  ! "${CAPS}" \
  ! jpegparse \
  ! nvv4l2decoder mjpeg=1 \
  ! nvvidconv \
  ! "video/x-raw,format=I420" \
  ! x264enc speed-preset=ultrafast tune=zerolatency \
      bitrate="${BITRATE_KBPS}" key-int-max=30 bframes=0 \
      option-string="scenecut=0:open-gop=0" \
  ! h264parse config-interval=1 \
  ! "video/x-h264,profile=baseline,alignment=au,stream-format=byte-stream" \
  ! tee name=t \
  t. ! queue ! filesink location="${H264_OUT}" \
  t. ! queue ! fpsdisplaysink video-sink=fakesink text-overlay=false sync="${SINK_SYNC}" \
  > "${FPS_LOG}" 2>&1 || true

elapsed=$(( SECONDS - started ))

if ! command -v ffprobe >/dev/null 2>&1; then
  echo "ffprobe 없음. 'sudo apt install -y ffmpeg' 후 재실행한다." >&2
  exit 1
fi

echo "=== FPS (합격 조건 1: >= 29) ==="
# fpsdisplaysink의 debug 출력은 min/max만 준다. 지속 처리량은 실제로 인코딩된
# 프레임 수를 세어 경과 시간으로 나눈다. sync=true 페이싱이라 인코더가 못
# 따라가면 프레임 수가 부족해진다.
#
# -count_frames는 전 프레임을 디코딩해서 10분 분량이면 수 분이 걸린다(외부
# timeout에 잘린다). -count_packets는 디코딩 없이 세므로 18000프레임도 0.5초다.
# alignment=au이므로 패킷 1개 = 프레임 1개다.
encoded_frames=$(ffprobe -v error -select_streams v:0 -count_packets \
  -show_entries stream=nb_read_packets -of csv=p=0 "${H264_OUT}" 2>/dev/null | tr -d ',')

if [[ -n "${encoded_frames}" && "${elapsed}" -gt 0 ]]; then
  awk -v f="${encoded_frames}" -v t="${elapsed}" -v target="${FPS}" 'BEGIN {
    fps = f / t
    printf "인코딩 프레임 : %d\n", f
    printf "경과 시간     : %d초\n", t
    printf "지속 처리량   : %.3f fps (목표 %s)\n", fps, target
    printf "판정          : %s\n", (fps >= 29 ? "합격 (>= 29)" : "미달 (< 29)")
  }'
else
  echo "프레임 수를 세지 못했다. ${H264_OUT}와 ${FPS_LOG}를 확인한다." >&2
fi

echo "--- fpsdisplaysink 순간값 (참고) ---"
grep -oE "min-fps to [0-9.]+" "${FPS_LOG}" | tail -1 | sed 's/min-fps to /최저 순간 FPS: /' \
  || echo "min-fps 기록 없음"
grep -oE "max-fps to [0-9.]+" "${FPS_LOG}" | tail -1 | sed 's/max-fps to /최고 순간 FPS: /' \
  || true

echo
echo "=== H.264 스트림 검증 (합격 조건 3) ==="

ffprobe -v error -select_streams v:0 \
  -show_entries stream=codec_name,profile,width,height,has_b_frames,avg_frame_rate \
  -of default=noprint_wrappers=1 "${H264_OUT}" | tee "${PROBE_LOG}"

echo "--- 키프레임 간격 (GOP 30 기대) ---"
ffprobe -v error -select_streams v:0 -show_entries frame=key_frame \
  -of csv=p=0 -read_intervals "%+#120" "${H264_OUT}" 2>/dev/null \
  | tr -d ',' | tr '\n' ' ' | awk '{
      first=-1; second=-1; n=0;
      for (i=1; i<=NF; i++) {
        if ($i==1) { n++; if (first<0) first=i; else if (second<0) second=i }
      }
      if (first>0 && second>0) printf "키프레임 간격: %d 프레임\n", second-first;
      else printf "샘플 120프레임 내 키프레임 %d개 — 간격 판정 불가\n", n;
    }'

echo
echo "판정 기준: FPS >= 29 / profile=Baseline 또는 Constrained Baseline /"
echo "           has_b_frames=0 / 키프레임 간격 30"
