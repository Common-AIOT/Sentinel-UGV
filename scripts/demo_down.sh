#!/usr/bin/env bash
#
# 데모 스택을 내린다 (S15P11A301-217). `demo_up.sh` 의 짝이다.
#
#   ./scripts/demo_down.sh
#   ./scripts/demo_down.sh --dry-run    # 무엇을 정리할지만 본다
#
# `stop_sentinel.sh` 와 역할이 다르다. 그쪽은 센서·스트리밍만 내리는 개발용이고
# 데모 스택의 3분의 1만 덮는다. 실측으로 확인한 결과 `demo.launch.py` 아래
# 14개가 뜨는데 그중 9개가 남았다 — 탐지(1601MB)와 slam_toolbox(551MB)만으로
# 2.1GB 다. 8GB 장비에서 다음 실행을 방해한다.
#
# **같은 사고가 두 번 났다.** S15P11A301-192 에서 "메모리 부족"의 원인을 VSCode 로
# 잘못 짚었는데, 실제 원인은 teardown 이 `src.ros_main`(탐지)을 빼먹어 CUDA
# 컨텍스트가 계속 잡혀 있던 것이었다. 그 앞에는 `usb_cam` 을 빼먹은 같은 사고가
# 있었다. 그때 고친 것은 임시 스크립트여서 저장소에 남지 않았다. 이 파일이 그
# 목록을 저장소에 두는 자리다.
#
# 그래서 **정리 후에 다시 훑어 확인하고, 남아 있으면 실패로 끝낸다.** "끄는 명령을
# 실행했다"와 "실제로 다 내려갔다"는 다르다.
set -Eeo pipefail

GRACE_SECONDS=20
TERM_WAIT_SECONDS=10

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  cat <<'USAGE'
Usage: ./scripts/demo_down.sh [--dry-run]

  --dry-run   정리 대상만 출력하고 아무것도 종료하지 않는다.
              패턴 누락을 스택을 내리지 않고 확인할 수 있다.

demo_up.sh 로 띄운 스택 전체를 내린다. 센서·스트리밍만 내리려면
stop_sentinel.sh 를 쓴다.
USAGE
  exit 0
fi

dry_run=0
if [[ "${1:-}" == "--dry-run" ]]; then
  dry_run=1
fi

self_pid=$$
parent_pid="${PPID}"

# launch 부모. 여기에 SIGINT 를 보내면 ros2 launch 가 자식들을 정상 종료시킨다.
# 녹화 파일을 쓰는 중일 수 있어 곧바로 KILL 하면 안 된다.
launch_patterns=(
  "sentinel_bringup demo.launch.py"
  "sentinel_bringup viz.launch.py"
)

# 정상 종료 후에도 남는 것을 훑는 목록이다. 돌고 있는 스택에서 실제로 열거한
# 프로세스 경로를 그대로 쓴다 — 짧은 이름으로 찾으면 무관한 것을 잡는다.
#
# 지금 안 떠 있는 cloud_bridge 와 foxglove_bridge 도 넣는다. 조건부로 뜨는
# 노드를 목록에서 빼면 그 조건이 켜진 날에만 남는다. 그런 것이 가장 찾기 어렵다.
node_patterns=(
  "lib/usb_cam/usb_cam_node_exe"
  "lib/ydlidar_ros2_driver/ydlidar_ros2_driver_node"
  "lib/robot_state_publisher/robot_state_publisher"
  "lib/tf2_ros/static_transform_publisher"
  "lib/slam_toolbox/async_slam_toolbox_node"
  "bin/mediamtx"
  "lib/sentinel_streaming/stream_pipeline"
  "lib/sentinel_recorder/recording_manager"
  "lib/sentinel_recorder/map_saver"
  "lib/sentinel_recorder/map_uploader"
  "lib/sentinel_recorder/media_uploader"
  "lib/sentinel_mission/mission_manager"
  "lib/sentinel_bridge/cloud_bridge"
  "sentinel_voice.ros_node"
  "-m src.ros_main"
  "foxglove_bridge"
)

# pgrep -f 는 패턴을 자기 명령줄에 가진 이 스크립트를 잡아 스스로 죽인다
# (S15P11A301-125). PID 를 먼저 모으고 자기 자신과 부모를 제외한다.
collect_pids() {
  local pattern="$1"
  # `-e` 가 반드시 있어야 한다. 패턴이 `-` 로 시작하면(예: `-m src.ros_main`)
  # grep 이 그것을 옵션으로 읽고 `invalid max count` 로 죽는다. 그러면 그 패턴만
  # 조용히 빠지는데, 하필 그것이 1601MB 탐지 노드였다 — S15P11A301-192 를 낸
  # 그 프로세스다. 이 스크립트를 만들면서 같은 함정에 다시 걸렸다.
  # shellcheck disable=SC2009  # 위 주석의 이유로 pgrep 을 쓰지 않는다
  ps -eo pid=,cmd= \
    | grep -F -e "${pattern}" \
    | grep -vF -e "grep" \
    | awk -v self="${self_pid}" -v parent="${parent_pid}" \
        '$1 != self && $1 != parent { print $1 }' || true
}

# 패턴 목록에 걸리는 PID 를 중복 없이 한 줄에 하나씩 낸다.
#
# nameref(local -n)로 배열에 직접 담지 않는다. ShellCheck 가 그 대입을 따라가지
# 못해 SC2154 를 다섯 군데에서 내고, disable 주석을 그만큼 붙이면 정작 진짜
# 경고가 묻힌다. 호출하는 쪽에서 mapfile 로 받는다.
collect_all() {
  local seen=" "
  local pattern pid
  for pattern in "$@"; do
    while read -r pid; do
      [[ -n "${pid}" ]] || continue
      [[ "${seen}" == *" ${pid} "* ]] && continue
      seen+="${pid} "
      printf '%s\n' "${pid}"
    done < <(collect_pids "${pattern}")
  done
}

describe() {
  local pid="$1"
  ps -o rss=,cmd= -p "${pid}" 2>/dev/null \
    | awk '{ rss = $1; $1 = ""; printf "%5dMB %s", rss / 1024, substr($0, 2) }' \
    | cut -c1-110 || true
}

used_mb() {
  free -m 2>/dev/null | awk '/^Mem:/ { print $3 }' || echo 0
}

any_alive() {
  local pid
  for pid in "$@"; do
    kill -0 "${pid}" 2>/dev/null && return 0
  done
  return 1
}

# ----------------------------------------------------------------------
# --dry-run
# ----------------------------------------------------------------------

if [[ "${dry_run}" -eq 1 ]]; then
  mapfile -t targets < <(collect_all "${launch_patterns[@]}" "${node_patterns[@]}")
  if [[ "${#targets[@]}" -eq 0 ]]; then
    echo "정리할 것이 없습니다."
    exit 0
  fi
  echo "정리 대상 ${#targets[@]}개 (--dry-run, 아무것도 종료하지 않습니다):"
  for pid in "${targets[@]}"; do
    printf '  %7d %s\n' "${pid}" "$(describe "${pid}")"
  done
  exit 0
fi

before_mb="$(used_mb)"

# ----------------------------------------------------------------------
# 1단계. launch 부모에 SIGINT — 정상 종료 경로
# ----------------------------------------------------------------------

mapfile -t launch_pids < <(collect_all "${launch_patterns[@]}")
if [[ "${#launch_pids[@]}" -gt 0 ]]; then
  echo "launch 프로세스에 SIGINT: ${launch_pids[*]}"
  kill -INT "${launch_pids[@]}" 2>/dev/null || true

  for _ in $(seq "${GRACE_SECONDS}"); do
    mapfile -t remaining < <(collect_all "${launch_patterns[@]}" "${node_patterns[@]}")
    [[ "${#remaining[@]}" -eq 0 ]] && break
    sleep 1
  done
fi

# ----------------------------------------------------------------------
# 2단계. 남은 것 TERM → KILL
# ----------------------------------------------------------------------

mapfile -t leftovers < <(collect_all "${launch_patterns[@]}" "${node_patterns[@]}")
if [[ "${#leftovers[@]}" -eq 0 ]]; then
  echo "정상 종료로 모두 내려갔습니다."
else
  echo "정상 종료 후 ${#leftovers[@]}개가 남아 TERM 을 보냅니다:"
  for pid in "${leftovers[@]}"; do
    printf '  %7d %s\n' "${pid}" "$(describe "${pid}")"
  done
  kill -TERM "${leftovers[@]}" 2>/dev/null || true

  for _ in $(seq "${TERM_WAIT_SECONDS}"); do
    any_alive "${leftovers[@]}" || break
    sleep 1
  done

  for pid in "${leftovers[@]}"; do
    if kill -0 "${pid}" 2>/dev/null; then
      echo "  TERM 에 응답하지 않아 KILL: ${pid}"
      kill -KILL "${pid}" 2>/dev/null || true
    fi
  done
  sleep 1
fi

# ----------------------------------------------------------------------
# 3단계. 확인 — 여기가 이 스크립트의 존재 이유다
# ----------------------------------------------------------------------

mapfile -t still < <(collect_all "${launch_patterns[@]}" "${node_patterns[@]}")
after_mb="$(used_mb)"
freed=$((before_mb - after_mb))

if [[ "${#still[@]}" -gt 0 ]]; then
  echo "실패: ${#still[@]}개가 아직 살아 있습니다." >&2
  for pid in "${still[@]}"; do
    printf '  %7d %s\n' "${pid}" "$(describe "${pid}")" >&2
  done
  echo "  KILL 에도 남는 것은 커널 대기 상태(D)일 수 있습니다. ps -o stat= 로 확인하십시오." >&2
  exit 1
fi

echo "모두 내려갔습니다."
if [[ "${freed}" -gt 0 ]]; then
  # 젯슨은 CPU·GPU 가 메모리를 공유하므로 free 가 GPU 회수까지 보여준다.
  echo "  회수된 메모리: ${freed}MB (사용 ${before_mb}MB → ${after_mb}MB)"
fi
