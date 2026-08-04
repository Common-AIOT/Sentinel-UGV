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

# viz 주소를 미리 알려 준다 (S15P11A301-224 후속).
#
# demo.launch.py 는 enable_viz 기본값이 true 라 이 스크립트 하나로 Foxglove
# 브릿지까지 뜨는데, 정작 어디로 붙으라는 말이 없었다. 그래서 사람들이
# viz_up.sh 의 안내 문구를 보러 갔고 그것이 낡아 있었다(ws:// 로 적혀 있었다).
#
# exec 뒤에는 아무것도 못 찍으므로 여기서 찍는다. 스킴은 실제 인자에서
# 계산한다 — 박아 두면 기본값이 바뀔 때 이 줄만 낡는다.
viz_enabled=1
viz_port=8765
viz_tls=true   # demo.launch.py → viz.launch.py 의 viz_tls 기본값
for arg in "$@"; do
  case "${arg}" in
    enable_viz:=false|enable_viz:=False|enable_viz:=0) viz_enabled=0 ;;
    viz_port:=*) viz_port="${arg#viz_port:=}" ;;
    viz_tls:=*) viz_tls="${arg#viz_tls:=}" ;;
  esac
done
if [[ "${viz_enabled}" -eq 1 ]]; then
  viz_scheme=ws
  if [[ "${viz_tls,,}" == "true" ]]; then
    viz_scheme=wss
  fi
  echo "Foxglove: ${viz_scheme}://jetson.sentinel-ugv.xyz:${viz_port}" \
       "(연결 유형 'Foxglove WebSocket')"
  if [[ "${viz_scheme}" == "wss" ]]; then
    echo "  ws:// 로 붙으면 핸드셰이크가 끊기고 오류에 이유가 안 남는다."
  fi
fi

exec ros2 launch sentinel_bringup demo.launch.py "$@"
