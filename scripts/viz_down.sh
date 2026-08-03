#!/usr/bin/env bash
#
# Foxglove 시각화를 끈다 (S15P11A301-216).
#
#   ./scripts/viz_down.sh
#
# 스택은 건드리지 않는다. Bridge 만 떼므로 SLAM 지도가 그대로 남는다.
#
# **`pkill -f foxglove_bridge` 를 쓰지 않는다.** 그 패턴 문자열이 호출한 셸의
# 명령줄에 들어 있어서 셸 자신이 함께 죽는다. 이 저장소에서 stop_sentinel.sh 가
# 같은 사고를 겪고 PID 를 먼저 모으는 방식으로 고쳤다(S15P11A301-125).
# 여기도 같은 방식이며, 자기 자신과 부모 프로세스를 명시적으로 제외한다.
#
# 이미 꺼져 있을 때 다시 부르는 것은 정상 사용이므로 성공으로 끝난다.
set -Eeo pipefail

DEFAULT_PORT="${SENTINEL_VIZ_PORT:-8765}"
LOG_FILE="${SENTINEL_VIZ_LOG:-/tmp/sentinel-viz.log}"

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  echo "Usage: ./scripts/viz_down.sh"
  echo "Foxglove Bridge 와 그 launch 프로세스를 정리한다. ROS 스택은 그대로 둔다."
  exit 0
fi

self_pid=$$
parent_pid="${PPID}"

# 실행 파일 경로와 launch 파일명으로 찾는다. 짧은 이름으로 찾으면 다른 것을 잡는다.
patterns=(
  "sentinel_bringup viz.launch.py"
  "foxglove_bridge"
)

# 끝의 `|| true` 가 없으면 안 된다. 일치하는 프로세스가 없을 때 grep 이 1을
# 반환하고 pipefail 이 이를 전파해 set -e 가 스크립트를 죽인다.
collect_pids() {
  local pattern="$1"
  # shellcheck disable=SC2009  # pgrep -f는 이 스크립트 자신을 잡아 스스로 죽인다
  ps -eo pid=,cmd= \
    | grep -F "${pattern}" \
    | grep -vF "grep" \
    | awk -v self="${self_pid}" -v parent="${parent_pid}" \
        '$1 != self && $1 != parent { print $1 }' || true
}

pids=()
for pattern in "${patterns[@]}"; do
  while read -r pid; do
    [[ -n "${pid}" ]] || continue
    pids+=("${pid}")
  done < <(collect_pids "${pattern}")
done

if [[ "${#pids[@]}" -eq 0 ]]; then
  echo "이미 꺼져 있습니다."
else
  # launch 프로세스를 먼저 보내면 자식 bridge 도 함께 정리되는 편이지만,
  # 순서를 신뢰하지 않고 모아서 보낸다.
  echo "정리할 PID: ${pids[*]}"
  kill -TERM "${pids[@]}" 2>/dev/null || true

  for _ in $(seq 10); do
    alive=0
    for pid in "${pids[@]}"; do
      if kill -0 "${pid}" 2>/dev/null; then
        alive=1
        break
      fi
    done
    [[ "${alive}" -eq 0 ]] && break
    sleep 1
  done

  # TERM 을 무시하는 것이 남아 있으면 KILL 한다. 남겨 두면 다음 viz_up.sh 가
  # "이미 켜져 있습니다"로 끝나 버리고 왜인지 알 수 없다.
  for pid in "${pids[@]}"; do
    if kill -0 "${pid}" 2>/dev/null; then
      echo "  TERM 에 응답하지 않아 KILL: ${pid}"
      kill -KILL "${pid}" 2>/dev/null || true
    fi
  done
fi

# 포트가 실제로 닫혔는지 확인한다. 프로세스를 지웠다는 것과 포트가 풀렸다는
# 것은 다르고, 안 풀리면 다음 viz_up.sh 가 조용히 아무것도 안 한다.
if command -v ss >/dev/null 2>&1; then
  for _ in $(seq 5); do
    if ! ss -tln 2>/dev/null \
      | awk -v pattern=":${DEFAULT_PORT}\$" '$4 ~ pattern { found = 1 } END { exit !found }'; then
      echo "${DEFAULT_PORT} 포트가 닫혔습니다."
      exit 0
    fi
    sleep 1
  done
  echo "경고: ${DEFAULT_PORT} 가 아직 열려 있습니다. 다른 프로세스가 쓰고 있을 수 있습니다." >&2
  echo "  로그: ${LOG_FILE}" >&2
  exit 1
fi

echo "완료 (ss 가 없어 포트는 확인하지 않았습니다)."
