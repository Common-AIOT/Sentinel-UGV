#!/usr/bin/env bash
# 시연 부하에서 안전 체인 lifecycle 경합이 재현되는지 관측한다 (S15P11A301-249).
#
# 배경: safety.launch.py 는 lifecycle_manager_safety 를 3초 늦게 띄워
# velocity_smoother 전이 경합을 피한다. 그 지연이 충분하다는 것은 **부하 0.5
# 에서만 확인됐다**(04-자율주행.md 1046~1052). 시연 부하(CPU 82~85%)에서도
# 유효한지는 미검증이며, 지면 체인이 가운데서 끊겨 명령이 모터까지 가지 않는다.
#
# 이 스크립트는 그 미검증 항목을 반복 관측으로 닫는다. **바퀴를 굴리지 않는다** —
# lifecycle 상태를 읽기만 하고 명령을 발행하지 않는다.
#
#   ./jetson/tests/lifecycle_race_probe.sh 5      # 5회 시행
#
# 각 시행은 스택 기동 → settle → 상태 조회 → 종료다. 결과는
# /home/orin/experiments/lifecycle-race/ 에 시행별로 남는다.
#
# 안전: enable_esp32:=false 로 모터 시리얼 경로를 끊은 채 기동한다. 그래도
# 바퀴를 띄우거나 모터 보드 전원을 분리한 상태에서 실행한다 — 이 차량에는
# 물리 E-Stop 이 없다.
set -Eeuo pipefail

TRIALS="${1:-5}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && cd .. && pwd)"
OUT_DIR="${OUT_DIR:-/home/orin/experiments/lifecycle-race}"
SETTLE_SECONDS="${SETTLE_SECONDS:-75}"

# 관리자가 돌보는 두 lifecycle 노드. 이름은 safety.launch.py 의 node_names 와
# 같아야 한다 — 바뀌면 여기도 함께 고친다.
NODES=(velocity_smoother collision_monitor)

mkdir -p "$OUT_DIR"

if ! command -v ros2 >/dev/null 2>&1; then
  echo "거부: ros2 를 찾을 수 없다. source /opt/ros/humble/setup.bash 후 실행한다." >&2
  exit 1
fi

echo "시행 $TRIALS 회, settle ${SETTLE_SECONDS}s, 출력 $OUT_DIR"
echo

for i in $(seq 1 "$TRIALS"); do
  trial_dir="$OUT_DIR/trial-$(printf '%02d' "$i")"
  mkdir -p "$trial_dir"
  echo "── 시행 $i/$TRIALS ──"

  # 이전 스택이 남아 있으면 내린다. 실패해도 계속 진행한다.
  "$REPO_ROOT/scripts/demo_down.sh" >"$trial_dir/down-before.log" 2>&1 || true
  sleep 5

  # 기동. 로그는 파일로 보내고 백그라운드로 둔다.
  "$REPO_ROOT/scripts/demo_up.sh" \
    enable_nav2:=true enable_safety:=true enable_esp32:=false \
    >"$trial_dir/stack.log" 2>&1 &
  stack_pid=$!

  # settle. 기동이 도중에 죽으면 기다리지 않고 넘어간다.
  for _ in $(seq 1 "$SETTLE_SECONDS"); do
    kill -0 "$stack_pid" 2>/dev/null || break
    sleep 1
  done

  {
    echo "trial=$i"
    echo "settle_seconds=$SETTLE_SECONDS"
    echo "uptime=$(uptime)"
    echo "loadavg=$(cut -d' ' -f1-3 /proc/loadavg)"
    echo "stack_alive=$(kill -0 "$stack_pid" 2>/dev/null && echo yes || echo no)"
  } >"$trial_dir/context.txt"

  # 핵심 관측: 두 노드의 lifecycle 상태. active [3] 이어야 한다.
  for node in "${NODES[@]}"; do
    state="$(timeout 15 ros2 lifecycle get "/$node" 2>&1 || echo 'QUERY_FAILED')"
    echo "$node: $state" | tee -a "$trial_dir/lifecycle.txt"
  done

  # 참고 관측: 관리자가 전이 응답 시각을 놓쳤는지 로그에서 확인한다.
  grep -c "failed to send response" "$trial_dir/stack.log" \
    >"$trial_dir/failed-response-count.txt" 2>/dev/null || echo 0 >"$trial_dir/failed-response-count.txt"

  "$REPO_ROOT/scripts/demo_down.sh" >"$trial_dir/down-after.log" 2>&1 || true
  wait "$stack_pid" 2>/dev/null || true
  sleep 5
  echo
done

echo "── 요약 ──"
grep -h . "$OUT_DIR"/trial-*/lifecycle.txt | sort | uniq -c | sort -rn
echo
echo "active [3] 이 아닌 시행이 하나라도 있으면 경합이 시연 부하에서 재현된 것이다."
echo "원본: $OUT_DIR"
