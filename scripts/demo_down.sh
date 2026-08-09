#!/usr/bin/env bash
#
# 데모 스택을 내린다 (S15P11A301-217). `demo_up.sh` 의 짝이다.
#
#   ./scripts/demo_down.sh
#   ./scripts/demo_down.sh --dry-run    # 무엇을 정리할지만 본다
#
# ## systemd 서비스가 켜져 있으면 프로세스를 죽여서는 내려가지 않는다
#
# **이것을 몰라서 "내렸는데 계속 돈다"를 겪었다(S15P11A301-294).** 2026-08-06
# 아침의 journalctl 이다.
#
#   09:20:24  demo_down.sh 실행 — 노드들 정상 종료 시작
#   09:20:28  systemd: Main process exited, code=exited, status=1/FAILURE
#   09:20:33  systemd: Scheduled restart job, restart counter is at 1
#
# launch 는 SIGINT 를 받으면 exit 1 로 끝난다. `sentinel-demo.service` 의
# `Restart=on-failure` 가 그것을 **고장으로 읽고** 5초 뒤 스택 전체를 되살린다.
# 즉 서비스가 active 인 동안 프로세스를 죽이는 경로는 **항상 무효**다.
#
# 그래서 서비스가 active 면 `systemctl stop` 으로 내린다. systemd 는 그것을
# 의도적 정지로 보고 재시작하지 않으며, cgroup 단위로 정리하므로 아래 패턴
# 목록보다 촘촘하다(패턴에서 빠질 프로세스가 없다).
#
# 프로세스 정리 경로를 지우지 않고 남기는 이유는, 서비스를 끈 개발 세션에서
# 손으로 올린 스택도 같은 명령으로 내려야 하기 때문이다. 서비스를 멈춘 뒤에도
# 아래 검사를 그대로 통과시키므로 손으로 띄운 잔여가 있으면 그것도 정리된다.
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
SERVICE_NAME=sentinel-demo.service

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  cat <<'USAGE'
Usage: ./scripts/demo_down.sh [--dry-run]

  --dry-run   정리 대상만 출력하고 아무것도 종료하지 않는다.
              패턴 누락을 스택을 내리지 않고 확인할 수 있다.

데모 스택 전체를 내린다. sentinel-demo.service 가 돌고 있으면 systemctl stop
경로로 내린다 — 프로세스만 죽이면 Restart=on-failure 가 5초 뒤 되살린다.
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

# 정상 종료 후에도 남는 것을 훑는 목록이다.
#
# **열거하지 않는다 — 접두사로 덮는다** (S15P11A301-351). 종전에는 노드를 하나씩
# 적었는데, 목록에 없는 노드가 살아남아도 이 스크립트가 「모두 내려갔습니다」라고
# 말했다. 2026-08-09 에 실제로 10개(sentinel 5 + nav2 5)가 남은 채 그 문구가 나왔고,
# 곧바로 demo_up.sh 가 「이미 돌고 있습니다」로 거부했다. 두 스크립트가 서로 다른
# 기준을 갖고 있었던 것이다 — demo_up.sh 는 S15P11A301-338 에서 넓은 패턴으로 짰다.
#
# 목록으로 관리하면 **패키지가 늘 때마다 여기를 고쳐야 하고, 안 고치면 조용히
# 남는다.** 없는 보호를 있다고 믿게 하는 형태다. 그래서 워크스페이스 설치 경로와
# nav2 설치 경로를 통째로 덮는다. 새 sentinel 패키지는 자동으로 포함된다.
#
# `collect_pids` 가 `grep -F`(고정 문자열)를 쓰므로 정규식은 못 쓴다. 접두사
# 문자열이면 충분하다 — 어차피 설치 경로가 고정이다.
node_patterns=(
  # 워크스페이스 install 아래 노드 전부.
  #
  # **`sentinel_` 접두사로 좁히지 않는다.** 이 워크스페이스에는 그 이름을 따르지
  # 않는 패키지가 있다 — `esp32_bridge` 가 그렇다. 2026-08-09 검증 중에
  # esp32_motor_bridge·esp32_sensor_bridge 둘이 살아남아 `demo_up.sh` 의 flock 을
  # 쥔 채로 남았고, 다음 기동이 「다른 demo_up.sh 가 이미 기동 중」으로 거부됐다.
  # 원인이 시리얼도 락 파일도 아니라 **정리 목록의 접두사**였다.
  "/ros2_ws/install/"
  # nav2 는 demo.launch.py 가 띄우므로 스택의 일부다. 종전 목록에 아예 없어서
  # controller_server·behavior_server·bt_navigator·waypoint_follower·
  # velocity_smoother 5개가 그대로 남았다.
  "/opt/ros/humble/lib/nav2_"
  # 아래는 위 두 접두사에 안 걸리는 것들이라 계속 열거한다.
  "lib/usb_cam/usb_cam_node_exe"
  "lib/ydlidar_ros2_driver/ydlidar_ros2_driver_node"
  "lib/robot_state_publisher/robot_state_publisher"
  "lib/tf2_ros/static_transform_publisher"
  "lib/slam_toolbox/async_slam_toolbox_node"
  "bin/mediamtx"
  # 지금 안 떠 있는 것도 넣는다. 조건부로 뜨는 노드를 목록에서 빼면 그 조건이
  # 켜진 날에만 남는다. 그런 것이 가장 찾기 어렵다.
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

service_active() {
  systemctl is-active --quiet "${SERVICE_NAME}" 2>/dev/null
}

service_enabled() {
  systemctl is-enabled --quiet "${SERVICE_NAME}" 2>/dev/null
}

# `systemctl stop` 은 root 권한이 필요하고 이 기기는 무암호 sudo 가 아니다.
# `-n` 으로 먼저 시도해 자격이 캐시돼 있으면 조용히 지나가고, 아니면 사람에게
# 암호를 묻는다. tty 가 없으면 sudo 가 즉시 실패하므로(멈추지 않는다) 자동화
# 경로에서도 걸리지 않는다.
stop_service() {
  if sudo -n systemctl stop "${SERVICE_NAME}" 2>/dev/null; then
    return 0
  fi
  echo "  sudo 암호가 필요합니다 (systemctl stop ${SERVICE_NAME})."
  sudo systemctl stop "${SERVICE_NAME}"
}

# ----------------------------------------------------------------------
# --dry-run
# ----------------------------------------------------------------------

if [[ "${dry_run}" -eq 1 ]]; then
  if service_active; then
    echo "${SERVICE_NAME} 가 스택을 돌리고 있습니다 — systemctl stop 경로로 내립니다."
    echo "  프로세스만 죽이면 Restart=on-failure 가 5초 뒤 되살립니다."
  fi
  mapfile -t targets < <(collect_all "${launch_patterns[@]}" "${node_patterns[@]}")
  if [[ "${#targets[@]}" -eq 0 ]]; then
    echo "정리할 것이 없습니다."
    exit 0
  fi
  echo "정리 대상 ${#targets[@]}개 (--dry-run, 아무것도 종료하지 않습니다):"
  for pid in "${targets[@]}"; do
    printf '  %7d %s\n' "${pid}" "$(describe "${pid}")"
  done
  # 잔여 DDS 세그먼트도 함께 보여준다(S15P11A301-338). 실제 정리는 4단계이고
  # 스택이 다 내려간 뒤에만 한다 — 여기서는 몇 개인지만 센다.
  shopt -s nullglob
  shm_seen=(/dev/shm/fastrtps_* /dev/shm/sem.fastrtps_*)
  shopt -u nullglob
  if [[ "${#shm_seen[@]}" -gt 0 ]]; then
    echo "  DDS 공유메모리 세그먼트 ${#shm_seen[@]}개 — 내린 뒤 사용자가 없는 것만 정리합니다."
  fi
  exit 0
fi

before_mb="$(used_mb)"

# ----------------------------------------------------------------------
# 0단계. 서비스가 돌고 있으면 그쪽으로 내린다
#
# 이 단계를 건너뛰고 아래 SIGINT 로 가면 systemd 가 5초 뒤 스택을 되살린다.
# 실패하면 여기서 끝낸다 — 헛되이 죽여 놓고 성공했다고 말하지 않는다.
# ----------------------------------------------------------------------

if service_active; then
  echo "${SERVICE_NAME} 정지 (프로세스만 죽이면 5초 뒤 되살아납니다)"
  if ! stop_service; then
    echo "실패: 서비스를 멈추지 못했습니다." >&2
    echo "  손으로 실행하십시오: sudo systemctl stop ${SERVICE_NAME}" >&2
    echo "  멈추지 않은 상태로 프로세스를 죽이면 5초 뒤 스택이 되살아납니다." >&2
    exit 1
  fi
fi

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

# ----------------------------------------------------------------------
# 4단계. 잔여 Fast DDS 공유메모리 세그먼트 정리 (S15P11A301-338)
#
# 프로세스가 강제 종료되면 /dev/shm 의 fastrtps 세그먼트가 남는다. 2026-08-07 에
# 스택 두 벌이 겹친 뒤 194개가 남았고, 그 상태에서 **새 참가자가 DDS 그래프에
# 붙지 못했다** — 노드는 다 살아 있는데 `ros2 node list` 가 0개였다. 용량 문제가
# 아니다(3.8G 중 20M). 남은 세그먼트가 포트를 잡고 있는 것이 원인이다.
#
# **`/dev/shm` 을 통째로 지우면 안 된다.** 이 기기에는 Tegra IPC 세그먼트가
# 함께 있다(nvsci*, nvmap_sciipc*, ipc_test*, itc_test* — 부팅 시 Tegra 스택이
# 만든다). 지우면 카메라·GPU 경로가 깨진다. 그래서 이름으로 fastrtps 만 고른다.
#
# 3단계를 통과한 뒤에만 온다 — 스택이 다 내려갔다는 것이 확인된 지점이다.
# 그래도 `fuser` 로 한 번 더 확인한다. 이 도메인 밖에서 누군가 `ros2 topic echo`
# 같은 것을 돌리고 있을 수 있고, **살아 있는 참가자의 세그먼트를 지우면 그
# 노드가 깨진다.** 사용자가 있는 것은 건드리지 않고 몇 개를 남겼는지 적는다.
# ----------------------------------------------------------------------
shopt -s nullglob
shm_stale=(/dev/shm/fastrtps_* /dev/shm/sem.fastrtps_*)
shopt -u nullglob

if [[ "${#shm_stale[@]}" -gt 0 ]]; then
  if ! command -v fuser >/dev/null 2>&1; then
    echo "  잔여 DDS 세그먼트 ${#shm_stale[@]}개를 두었습니다 — fuser 가 없어" \
         "사용 중인지 확인할 수 없습니다(psmisc)."
    echo "  새 스택이 그래프에 못 붙으면 스택을 모두 내린 뒤" \
         "rm -f /dev/shm/fastrtps_* /dev/shm/sem.fastrtps_* 하십시오."
  else
    removed=0
    inuse=0
    for seg in "${shm_stale[@]}"; do
      if fuser -s "${seg}" 2>/dev/null; then
        inuse=$((inuse + 1))
      elif rm -f "${seg}" 2>/dev/null; then
        removed=$((removed + 1))
      fi
    done
    if [[ "${removed}" -gt 0 ]]; then
      echo "  잔여 DDS 세그먼트 ${removed}개 정리(Tegra IPC 세그먼트는 그대로)."
    fi
    if [[ "${inuse}" -gt 0 ]]; then
      echo "  사용 중인 세그먼트 ${inuse}개는 남겼습니다 — 다른 ROS 프로세스가" \
           "있는지 확인하십시오."
    fi
  fi
fi

# 꺼진 채로 유지되는 범위를 분명히 한다. 서비스가 enabled 인 동안 "내렸다"는
# 다음 부팅까지만 참이다. 이것을 모르면 재부팅 뒤에 "왜 또 돌지"가 된다.
if service_enabled; then
  echo "  ${SERVICE_NAME} 는 enabled 입니다 — 재부팅하면 다시 올라옵니다."
  echo "  부팅 자동 기동까지 끄려면: sudo systemctl disable ${SERVICE_NAME}"
  echo "  다시 올리려면          : sudo systemctl start ${SERVICE_NAME}"
else
  echo "  다시 올리려면: ./scripts/demo_up.sh"
fi
