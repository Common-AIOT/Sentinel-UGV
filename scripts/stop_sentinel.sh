#!/usr/bin/env bash
#
# Sentinel UGV의 젯슨 측 ROS 스택을 끈다 (S15P11A301-125).
#
# pkill -f 로 정리하다 이 스크립트 자신이 죽는 사고가 있었다. 패턴 문자열이
# 스크립트의 명령줄에 들어 있기 때문이다. 그래서 PID를 먼저 모으고 그
# PID만 신호로 정리한다.
set -Eeo pipefail

if [[ "${1:-}" == "--help" ]]; then
  echo "Usage: ./scripts/stop_sentinel.sh"
  echo "카메라, stream_pipeline, MediaMTX와 그 launch 프로세스를 정리한다."
  exit 0
fi

self_pid=$$

# 실행 파일 경로로 찾는다. 짧은 이름으로 찾으면 다른 프로세스를 잡는다.
#
# robot_state_publisher를 빼먹으면 안 된다. sensors.launch.py → lidar.launch.py
# → description.launch.py 순으로 include되어 함께 뜨는데, 정리 목록에 없으면
# start/stop을 돌 때마다 고아가 하나씩 쌓인다. 그것들이 TF를 중복 발행한다.
# 실제로 3개까지 누적된 것을 확인했다(S15P11A301-125).
patterns=(
  "lib/sentinel_streaming/stream_pipeline"
  "usb_cam_node_exe"
  "ydlidar_ros2_driver_node"
  "robot_state_publisher"
  "sentinel_bringup sensors.launch.py"
  "sentinel_streaming streaming.launch.py"
  "bin/mediamtx"
)

# 끝의 `|| true`가 없으면 안 된다. 일치하는 프로세스가 없을 때 grep이 1을
# 반환하고 pipefail이 이를 전파해 set -e가 스크립트를 죽인다. 이미 정지된
# 상태에서 다시 부르는 것은 정상 사용이다.
collect_pids() {
  local pattern="$1"
  # shellcheck disable=SC2009  # pgrep -f는 이 스크립트 자신을 잡아 스스로 죽인다
  ps -eo pid=,cmd= \
    | grep -F "${pattern}" \
    | grep -vF "grep" \
    | awk -v self="${self_pid}" '$1 != self {print $1}' || true
}

all_pids=()
for pattern in "${patterns[@]}"; do
  while read -r pid; do
    [[ -n "${pid}" ]] && all_pids+=("${pid}")
  done < <(collect_pids "${pattern}")
done

if [[ "${#all_pids[@]}" -eq 0 ]]; then
  echo "정리할 프로세스가 없다."
  exit 0
fi

# 중복 제거. launch 프로세스가 여러 패턴에 걸릴 수 있다.
mapfile -t unique_pids < <(printf '%s\n' "${all_pids[@]}" | sort -u -n)

echo "정리 대상 ${#unique_pids[@]}개:"
for pid in "${unique_pids[@]}"; do
  cmd="$(ps -p "${pid}" -o cmd= 2>/dev/null | cut -c1-88)"
  [[ -n "${cmd}" ]] && printf "  %-8s %s\n" "${pid}" "${cmd}"
done

# SIGTERM을 먼저 준다. launch가 자식 노드를 정리할 기회를 줘야 하고,
# 특히 stream_pipeline은 GStreamer 파이프라인을 닫아야 한다.
kill -TERM "${unique_pids[@]}" 2>/dev/null || true

deadline=$((SECONDS + 10))
while (( SECONDS < deadline )); do
  alive=0
  for pid in "${unique_pids[@]}"; do
    kill -0 "${pid}" 2>/dev/null && alive=1 && break
  done
  [[ "${alive}" -eq 0 ]] && break
  sleep 1
done

# 10초를 버티면 강제 종료한다.
remaining=()
for pid in "${unique_pids[@]}"; do
  kill -0 "${pid}" 2>/dev/null && remaining+=("${pid}")
done

if [[ "${#remaining[@]}" -gt 0 ]]; then
  echo "10초 안에 종료되지 않은 ${#remaining[@]}개를 강제 종료한다: ${remaining[*]}"
  kill -KILL "${remaining[@]}" 2>/dev/null || true
  sleep 1
fi

# 남은 것이 없는지 확인한다. 하나라도 남으면 다음 start가 중복으로 뜬다.
leftover=()
for pattern in "${patterns[@]}"; do
  while read -r pid; do
    [[ -n "${pid}" ]] && leftover+=("${pid}")
  done < <(collect_pids "${pattern}")
done

if [[ "${#leftover[@]}" -gt 0 ]]; then
  echo "정리하지 못한 프로세스가 남았다: ${leftover[*]}" >&2
  exit 1
fi

echo "정리 완료."
