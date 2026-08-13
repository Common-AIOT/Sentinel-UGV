#!/usr/bin/env bash
# CI의 test:message-contract 를 로컬에서 그대로 돌린다 (S15P11A301-135).
#
# 젯슨에서 그냥 pytest를 돌리면 통과하는데 CI에서 깨지는 일이 반복됐습니다.
# 원인은 환경 차이 두 가지입니다.
#
#   1. 젯슨에는 ROS가 source돼 있어 PYTHONPATH로 rclpy가 보인다
#   2. 시스템 파이썬에 requests·numpy 같은 것이 이미 깔려 있다
#
# CI 컨테이너(python:3.10-alpine)에는 둘 다 없습니다. 그래서 여기서는 전용 venv를
# 만들고 `env -i`로 환경변수를 완전히 비워 돌립니다.
#
# 계약 시험이나 common/schemas 를 건드렸으면 푸시 전에 이것을 돌립니다.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

VENV="${CONTRACT_TEST_VENV:-${TMPDIR:-/tmp}/sentinel-contract-venv}"
REQUIREMENTS="scripts/ci/contract-test-requirements.txt"

# 검증 대상 패키지. .gitlab-ci.yml 의 script 와 같아야 한다.
PACKAGES=(
  esp32_bridge
  sentinel_bridge
  sentinel_drive
  sentinel_mission
  sentinel_recorder
  sentinel_safety
  sentinel_streaming
)


if [[ ! -x "$VENV/bin/python" ]]; then
  echo "CI 환경을 흉내낼 venv를 만든다: $VENV"
  python3 -m venv "$VENV"
  "$VENV/bin/pip" install --quiet --upgrade pip
  "$VENV/bin/pip" install --quiet -r "$REQUIREMENTS"
else
  # 요구 파일이 바뀌었을 수 있으므로 매번 맞춘다. 이미 맞으면 즉시 끝난다.
  "$VENV/bin/pip" install --quiet -r "$REQUIREMENTS"
fi

PY="$VENV/bin/python"

# ROS가 새어 들어오지 않는지 먼저 확인한다. 보이면 이 스크립트가 CI를 재현하지
# 못하는 것이므로 조용히 통과시키지 않는다.
if env -i HOME="$HOME" "$PY" -c "import rclpy" 2>/dev/null; then
  echo "rclpy가 보인다. CI 환경을 재현하지 못했다." >&2
  exit 1
fi

echo "== 1. 스키마와 예제 =="
env -i HOME="$HOME" PATH=/usr/bin:/bin "$PY" scripts/ci/validate_schemas.py

failed=0
for package in "${PACKAGES[@]}"; do
  target="jetson/ros2_ws/src/$package/test"
  if [[ ! -d "$target" ]]; then
    echo "== $package 건너뜀 (시험 디렉터리 없음) =="
    continue
  fi
  echo "== 2. $package =="
  if ! env -i HOME="$HOME" PATH=/usr/bin:/bin "$PY" -m pytest "$target" -q; then
    failed=1
  fi
done

if [[ "$failed" -ne 0 ]]; then
  echo
  echo "실패했다. CI도 같은 결과가 나온다." >&2
  exit 1
fi

echo
echo "CI의 test:message-contract 와 같은 조건으로 통과했다."
