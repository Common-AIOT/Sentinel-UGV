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
esp32_specified=0
for arg in "$@"; do
  case "${arg}" in
    enable_viz:=false|enable_viz:=False|enable_viz:=0) viz_enabled=0 ;;
    viz_port:=*) viz_port="${arg#viz_port:=}" ;;
    viz_tls:=*) viz_tls="${arg#viz_tls:=}" ;;
    enable_esp32:=*) esp32_specified=1 ;;
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

# ESP32 센서 보드 자동 감지 (S15P11A301-256).
#
# demo.launch.py 의 enable_esp32 기본값은 false 다. 그 이유는 켜면 slam 의
# static identity 가 꺼지기 때문이다 — 보드가 없는데 켜면 odom TF 발행자가
# 0개가 되고 slam_toolbox 가 지도를 아예 만들지 않는다. 브리지는 죽지 않고
# 재접속을 재시도하므로 프로세스와 토픽은 정상으로 보이고, 증상은 "지도가
# 안 나온다" 하나뿐이다. 그래서 기본을 꺼 두는 것이 옳았다.
#
# 그 기본값의 대가는 사람이 매번 enable_esp32:=true 를 기억해야 한다는 것이고,
# 잊으면 /environment/* 가 조용히 안 나온다(S15P11A301-213 이 값을 못 받던
# 이유가 그것이다). S15P11A301-214 가 udev 별칭을 만든 뒤로는 보드 유무를
# 장치 경로로 확인할 수 있으므로, 기억이 아니라 하드웨어가 결정하게 한다.
#
# 센서 보드를 보는 이유: odom TF 를 내는 쪽이 esp32_sensor_bridge 다
# (/wheel/odometry 와 /tf 를 그것이 발행한다). 모터 보드는 static identity
# 판단과 무관하므로 감지 조건에 넣지 않는다.
SENSOR_DEV=/dev/sentinel_mcu_sensor   # scripts/udev/99-sentinel-mcu.rules
if [[ "${esp32_specified}" -eq 1 ]]; then
  # 사람이 명시했으면 그것이 이긴다. 보드가 없는 채로 켜서 실패를 재현하는
  # 것도 정당한 사용이다(위 오진 경로 확인).
  echo "ESP32: 인자로 지정됨 — 자동 감지를 건너뛴다."
elif [[ -e "${SENSOR_DEV}" ]]; then
  set -- "$@" enable_esp32:=true
  echo "ESP32: 센서 보드 감지(${SENSOR_DEV}) — enable_esp32:=true 로 켠다."
else
  echo "ESP32: 센서 보드 없음(${SENSOR_DEV}) — 끈 채로 간다." \
       "SLAM 은 static identity 로 돌고 /environment/* 는 발행되지 않는다."
fi

exec ros2 launch sentinel_bringup demo.launch.py "$@"
