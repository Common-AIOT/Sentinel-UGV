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

# set -u 와 ROS setup.bash 는 함께 못 쓴다. 소싱 동안만 푼다.
set +u
# 설치된 ROS 환경 파일은 저장소 밖에 있어 ShellCheck가 따라갈 수 없다.
# shellcheck disable=SC1091
source /opt/ros/humble/setup.bash
# 빌드 후 생성되는 setup 파일이므로 정적 분석 시에는 존재하지 않을 수 있다.
# shellcheck disable=SC1091
source "${REPO_ROOT}/jetson/ros2_ws/install/setup.bash"
set -u

exec ros2 launch sentinel_bringup demo.launch.py "$@"
