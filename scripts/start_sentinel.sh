#!/usr/bin/env bash
#
# Sentinel UGV의 젯슨 측 ROS 스택을 켠다 (S15P11A301-125).
#
# 이름을 sentinel로 둔 이유는 켜는 대상이 스트리밍만이 아니기 때문이다. 지금은
# 센서와 스트리밍이고, 여기에 녹화(S15P11A301-123)와 AI·임무 노드가 붙는다.
# 명세 37-3의 systemd 유닛도 sentinel-* 접두어이므로 계보가 이어진다.
#
# 손으로 켜면 터미널 두 개에서 source 두 번과 launch 두 번을 입력해야 하고,
# 그 과정에서 stream_pipeline 노드가 두 개 뜨는 사고가 실제로 있었다.
# MediaMTX는 한 경로에 발행자 하나만 허용하므로 두 노드가 경로를 빼앗으며
# 재구성을 반복하고, 증상이 네트워크 문제처럼 보여 원인을 찾기 어렵다.
# 그래서 이 스크립트는 켜기 전에 항상 중복을 확인한다.
#
# 센서는 카메라만이 아니다. sensors.launch.py가 lidar.launch.py를 include하고
# 그것이 다시 description.launch.py를 include하므로, 한 번에 셋이 뜬다.
#
#   usb_cam                → /camera/image_raw/compressed
#   ydlidar_ros2_driver    → /scan
#   robot_state_publisher  → /tf, /tf_static, /robot_description
#
# 사용법:
#   ./scripts/start_sentinel.sh                  # HTTPS (기본)
#   ./scripts/start_sentinel.sh --no-tls         # 평문 HTTP
#   ./scripts/start_sentinel.sh --sensors-only   # 센서만, 스트리밍 없이
#
# set -u는 쓰지 않는다. ROS의 setup.bash가 AMENT_TRACE_SETUP_FILES 같은
# 미설정 변수를 참조해서 즉시 죽는다.
set -Eeo pipefail

encryption="true"
sensors_only="false"

for arg in "$@"; do
  case "${arg}" in
    --help)
      echo "Usage: ./scripts/start_sentinel.sh [--no-tls] [--sensors-only]"
      echo "  (default)        센서 + 스트리밍, WebRTC는 HTTPS"
      echo "  --no-tls         WebRTC를 평문 HTTP로 띄운다"
      echo "  --sensors-only   센서만 띄우고 스트리밍은 띄우지 않는다"
      echo
      echo "센서는 카메라, 라이다, robot_state_publisher 셋이다."
      exit 0
      ;;
    --no-tls)       encryption="false" ;;
    --sensors-only) sensors_only="true" ;;
    *)
      echo "알 수 없는 인자: ${arg}. --help 를 참고한다." >&2
      exit 1
      ;;
  esac
done

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workspace="${repo_root}/jetson/ros2_ws"
log_dir="${SENTINEL_LOG_DIR:-${workspace}/log/sentinel}"

# 중복 노드 판정.
#
# pgrep -f 를 쓰면 패턴 문자열을 명령줄에 가진 이 스크립트 자신까지 세어
# 항상 1 이상이 나온다. 실행 파일 경로로 ps를 걸러야 정확하다.
#
# 끝의 `|| true`가 없으면 안 된다. 아무것도 안 돌고 있을 때 grep이 1을
# 반환하고, pipefail 때문에 명령 치환이 실패해 set -e가 스크립트를 조용히
# 죽인다. 그것이 바로 이 스크립트를 정상적으로 쓰는 경우다.
pids_matching() {
  # shellcheck disable=SC2009  # pgrep을 피하는 것이 이 함수의 존재 이유다
  ps -eo pid=,cmd= \
    | grep -F "$1" \
    | grep -vF "grep" \
    | awk '{print $1}' || true
}

existing_stream="$(pids_matching "lib/sentinel_streaming/stream_pipeline" | tr '\n' ' ')"
existing_camera="$(pids_matching "usb_cam_node_exe" | tr '\n' ' ')"
existing_lidar="$(pids_matching "ydlidar_ros2_driver_node" | tr '\n' ' ')"
# robot_state_publisher는 고아로 남기 쉬우므로 따로 본다. 중복되면 같은 TF를
# 두 번 발행해 하위 노드가 흔들린다.
existing_rsp="$(pids_matching "robot_state_publisher" | tr '\n' ' ')"

if [[ -n "${existing_stream// /}" || -n "${existing_camera// /}" \
   || -n "${existing_lidar// /}"  || -n "${existing_rsp// /}" ]]; then
  echo "이미 실행 중이거나 이전 실행이 남아 있다." >&2
  [[ -n "${existing_camera// /}" ]] && echo "  카메라                PID ${existing_camera}" >&2
  [[ -n "${existing_lidar// /}"  ]] && echo "  라이다                PID ${existing_lidar}" >&2
  [[ -n "${existing_rsp// /}"    ]] && echo "  robot_state_publisher PID ${existing_rsp}" >&2
  [[ -n "${existing_stream// /}" ]] && echo "  stream_pipeline       PID ${existing_stream}" >&2
  echo >&2
  echo "정지 후 다시 켠다: ./scripts/stop_sentinel.sh && ./scripts/start_sentinel.sh" >&2
  exit 1
fi

if [[ ! -f "${workspace}/install/setup.bash" ]]; then
  echo "워크스페이스가 빌드되지 않았다: ${workspace}/install" >&2
  echo "  cd ${workspace} && colcon build --symlink-install" >&2
  exit 1
fi

# 패치가 빠진 채로 빌드되어 있으면 압축 토픽이 재인코딩된 깨진 데이터가 된다.
# 증상이 카메라 문제로 보이므로 켜기 전에 확인한다.
if ! "${repo_root}/scripts/setup_jetson.sh" --check >/dev/null 2>&1; then
  echo "필수 패치 또는 MediaMTX 확인에 실패했다." >&2
  echo "  ./scripts/setup_jetson.sh 를 실행해 해결한 뒤 다시 시도한다." >&2
  exit 1
fi

mkdir -p "${log_dir}"

# ROS 소싱과 DDS 격리 설정(S15P11A301-218). demo_up.sh 와 같은 파일을 쓴다 —
# 두 진입점의 도메인이 다르면 한쪽으로 띄운 노드를 다른 쪽 도구가 못 본다.
# shellcheck source=ros_env.sh disable=SC1091
source "${repo_root}/scripts/ros_env.sh"

echo "센서를 켠다 (카메라, 라이다, robot_state_publisher)..."
nohup ros2 launch sentinel_bringup sensors.launch.py \
  >"${log_dir}/sensors.log" 2>&1 &
sensors_launch_pid=$!
echo "  launch PID ${sensors_launch_pid}, 로그 ${log_dir}/sensors.log"

# 프레임이 오기 전에 스트리밍을 띄우면 파이프라인이 EOS를 받아 재구성을
# 한 번 겪는다. 기능상 복구되지만 로그가 지저분해지고, 진짜 장애와
# 구분하기 어려워진다. 토픽이 살아난 뒤에 다음 단계로 간다.
echo "센서 토픽을 기다린다..."
deadline=$((SECONDS + 25))
until ros2 topic list 2>/dev/null | grep -qx "/camera/image_raw/compressed"; do
  if ! kill -0 "${sensors_launch_pid}" 2>/dev/null; then
    echo "센서 launch가 죽었다. 로그를 확인한다: ${log_dir}/sensors.log" >&2
    exit 1
  fi
  if (( SECONDS >= deadline )); then
    echo "카메라 토픽이 25초 안에 올라오지 않았다: ${log_dir}/sensors.log" >&2
    exit 1
  fi
  sleep 1
done
echo "  /camera/image_raw/compressed 확인"

# 라이다는 없어도 스트리밍은 되므로 실패로 다루지 않는다. 다만 조용히 빠지면
# SLAM 쪽에서 원인을 찾느라 시간을 버리므로 여기서 알린다.
if ros2 topic list 2>/dev/null | grep -qx "/scan"; then
  echo "  /scan 확인"
else
  echo "  경고: /scan이 올라오지 않았다. 라이다 연결과 ${log_dir}/sensors.log 를 확인한다." >&2
fi

if [[ "${sensors_only}" == "true" ]]; then
  echo
  echo "센서만 켰다. 정지: ./scripts/stop_sentinel.sh"
  exit 0
fi

echo "스트리밍과 MediaMTX를 켠다..."
nohup ros2 launch sentinel_streaming streaming.launch.py \
  "webrtc_encryption:=${encryption}" \
  >"${log_dir}/streaming.log" 2>&1 &
streaming_launch_pid=$!
echo "  launch PID ${streaming_launch_pid}, 로그 ${log_dir}/streaming.log"

scheme="https"
[[ "${encryption}" == "true" ]] || scheme="http"

echo "WHEP 엔드포인트를 기다린다..."
deadline=$((SECONDS + 25))
until curl -sk -o /dev/null --max-time 2 \
    -X OPTIONS "${scheme}://127.0.0.1:8889/sentinel/whep" 2>/dev/null; do
  if ! kill -0 "${streaming_launch_pid}" 2>/dev/null; then
    echo "스트리밍 launch가 죽었다. 로그를 확인한다: ${log_dir}/streaming.log" >&2
    exit 1
  fi
  if (( SECONDS >= deadline )); then
    echo "WHEP 엔드포인트가 25초 안에 응답하지 않았다: ${log_dir}/streaming.log" >&2
    exit 1
  fi
  sleep 1
done

# 관제 웹은 브라우저에서 접속하므로 127.0.0.1이 아니라 실제 IP가 필요하다.
# VS Code 포트 포워딩된 주소로 열면 WHEP POST가 막힌다.
host_ip="$(hostname -I | awk '{print $1}')"

# 관제 웹의 WHEP 주소는 frontend/.env.local에 IP로 박혀 있다. DHCP가 주소를
# 바꾸면 화면은 "연결 중"에서 멈추고 서버 로그에는 아무것도 남지 않는다.
# 원인을 찾기 어려운 종류이므로 여기서 미리 대조한다.
env_file="${repo_root}/frontend/.env.local"
env_key="NEXT_PUBLIC_LOCAL_STREAM_URL"
expected_url="${scheme}://${host_ip}:8889/sentinel/whep"

if [[ ! -f "${env_file}" ]]; then
  echo "경고: ${env_file} 이 없다. 관제 웹이 스트림 주소를 모른다." >&2
  echo "      echo '${env_key}=${expected_url}' > ${env_file}" >&2
else
  configured_url="$(grep -E "^${env_key}=" "${env_file}" | tail -1 | cut -d= -f2- || true)"
  if [[ "${configured_url}" != "${expected_url}" ]]; then
    echo "경고: ${env_key} 가 현재 주소와 다르다." >&2
    echo "      설정값 ${configured_url:-(없음)}" >&2
    echo "      현재값 ${expected_url}" >&2
    echo "      고치지 않으면 관제 화면이 연결 중에서 멈춘다. 고친 뒤 dev 서버를" >&2
    echo "      다시 시작해야 한다. Next.js는 NEXT_PUBLIC_ 값을 빌드 시점에 넣는다." >&2
  fi
fi

echo
echo "준비 완료."
echo "  WHEP        ${expected_url}"
echo "  내장 플레이어 ${scheme}://${host_ip}:8889/sentinel"
echo "  관제 웹      http://${host_ip}:3000"
echo
echo "관제 웹은 따로 켠다. next dev가 이미 모든 인터페이스에 바인딩하므로"
echo "감싸는 스크립트를 두지 않는다."
echo "  cd frontend && npm run dev"
echo
echo "정지: ./scripts/stop_sentinel.sh"
