#!/usr/bin/env bash
# 데모 스택 진입점 (S15P11A301-156). systemd 유닛 sentinel-demo 가 이것을 부른다.
#
# 손으로 올릴 때도 이것 하나면 된다:
#   ./scripts/demo_up.sh
#   ./scripts/demo_up.sh enable_detector:=false     # 인자는 그대로 전달된다
#
# 선행 조건 (한 번만, sudo 필요):
#   sudo mkdir -p /var/lib/sentinel/media && sudo chown -R orin:orin /var/lib/sentinel
#   ~/.config/sentinel/secrets.yaml (600) 에 broker_password
#   ~/.config/sentinel/certs/server.{crt,key}  (S15P11A301-145)
set -Eeuo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export SENTINEL_REPO_ROOT="${REPO_ROOT}"

# ROS 소싱과 DDS 격리 설정(S15P11A301-218). 값은 그 파일에만 있다 — 여기에
# 복사하면 두 곳이 어긋나고, 어긋나면 노드들이 서로를 못 본 채 조용히 돈다.
# ShellCheck 는 -x 없이는 source 를 따라가지 못한다(SC1091). CI 는 기본
# 심각도로 돌아 info 도 실패로 다루므로 이 파일의 다른 ROS 소싱과 같이 끈다.
# shellcheck source=ros_env.sh disable=SC1091
source "${REPO_ROOT}/scripts/ros_env.sh"

exec ros2 launch sentinel_bringup demo.launch.py "$@"
