# shellcheck shell=bash
#
# 공통 ROS 환경 (S15P11A301-218). **source 전용이다.**
#
#   source "$(dirname "${BASH_SOURCE[0]}")/ros_env.sh"
#
# ## 왜 값이 여기 한 곳에만 있는가
#
# ROS_DOMAIN_ID·ROS_LOCALHOST_ONLY 가 프로세스마다 다르면 노드들이 서로를 못
# 본다. 그때 증상은 "노드가 안 뜬다"가 아니라 **"토픽이 조용히 비어 있다"** 다.
# 발행도 성공하고 구독도 성공하는데 상대가 없을 뿐이라 로그에 아무것도 안 남는다.
# 원인을 찾기 가장 어려운 형태이므로, 값을 스크립트마다 복사하지 않고 이 파일만
# 고치게 한다.
#
# 손으로 `ros2` 명령을 칠 때도 같은 환경이 필요하다. scripts/README.md 참고.
#
# ## 왜 격리하는가
#
# 이 설정이 비어 있는 동안 **다른 팀의 ROS 그래프가 우리 젯슨과 섞여 있었다.**
# `ros2 node list` 에 남의 nav2 스택 두 벌(`/sim_f02/*`, `/sim_f03/*`)과
# `/kinematic_fleet`, `/rviz`, `/map_server` 가 있었다. DDS 는 LAN 멀티캐스트로
# 디스커버리하므로 같은 망 + 같은 도메인이면 서로를 찾는다. 기본값 0 에 여러 팀이
# 함께 있었다.
#
# 실제 피해가 확인된 곳:
#
#   /map        발행자 2 (우리 slam_toolbox + 남의 map_server).
#               map_saver 가 어느 지도를 저장할지 보장이 없었다. latched 토픽이라
#               늦게 붙은 구독자가 남의 retained 메시지를 받을 수 있다.
#   /tf         발행자 4, /tf_static 5. TF 트리가 섞여 Foxglove 3D 를 읽을 수
#               없었다 — 남의 로봇 두 대 프레임이 우리 것 위에 겹쳐 그려졌다.
#   /cmd_vel    남의 /kinematic_fleet 이 구독. 주행 코드를 붙이면 우리 명령이
#               남의 시뮬레이터로 간다. 실물 모터가 달린 로봇이라 안전 문제다.

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  echo "ros_env.sh 는 source 전용입니다. 직접 실행해도 효과가 없습니다." >&2
  echo "  source scripts/ros_env.sh" >&2
  exit 1
fi

# ROS 통신이 전부 젯슨 안에서 끝나므로 루프백으로 묶어도 잃는 것이 없다. 밖으로
# 나가는 것은 MQTT·HTTPS·WHEP(젯슨 8889)·Foxglove WebSocket(8765)이고 모두 DDS 가
# 아니다. Foxglove 는 bridge 가 젯슨 내부 ROS 노드이므로 그대로 동작한다.
#
# 이 설정의 알려진 함정은 컨테이너 간 통신 차단인데 해당하지 않는다. 젯슨의 ROS
# 프로세스는 전부 호스트 네트워크 네임스페이스이고 도커는 백엔드뿐이며 EC2 에서 돈다.
#
# 도메인 격리(합의)와 달리 이것은 **물리적 차단**이다. 다른 팀이 우리와 같은
# 도메인 번호를 고르더라도 들어올 수 없다.
export ROS_LOCALHOST_ONLY=1

# 이중 방어다. ROS_LOCALHOST_ONLY 는 Humble 이후 폐기 예정이라 배포를 올릴 때
# 조용히 무력화될 수 있고, 나중에 기기 간 DDS 가 필요해지면 이쪽만 남기고
# 전환하면 된다.
#
# 값은 0~101 범위여야 한다. 그 위는 리눅스 임시 포트 범위와 겹쳐(포트가 대략
# 7400 + 250×도메인 으로 계산된다) 산발적인 디스커버리 실패를 만든다. 0 이 아닌
# 높은 값을 고른 이유는 0 부터 하나씩 올려 쓰는 다른 팀과 부딪히지 않기 위해서다.
export ROS_DOMAIN_ID=97

# ---------------------------------------------------------------------------
# ROS 와 워크스페이스 소싱
#
# 진입점들이 같은 절차를 각자 복사해 두고 있었다. 한 곳에서만 관리한다.
# (부분 기동 진입점은 S15P11A301-294 에서 제거했고 지금 소싱하는 쪽은
#  demo_up·viz_up·jetson_voice_preflight 다.)
# ---------------------------------------------------------------------------

SENTINEL_ROS_ENV_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# set -u 와 ROS setup.bash 는 함께 못 쓴다. 소싱 동안만 푼다.
#
# 호출한 스크립트의 -u 설정을 그대로 되돌려야 한다. 무조건 `set -u` 로 끝내면
# -u 를 쓰지 않는 스크립트의 동작을 바꿔 버린다.
sentinel_ros_env_had_u=0
case "$-" in
  *u*) sentinel_ros_env_had_u=1 ;;
esac

set +u
# 설치된 ROS 환경 파일은 저장소 밖에 있어 ShellCheck 가 따라갈 수 없다.
# shellcheck disable=SC1091
source /opt/ros/humble/setup.bash
# 빌드 후 생성되는 setup 파일이므로 정적 분석 시에는 존재하지 않을 수 있다.
# shellcheck disable=SC1091
source "${SENTINEL_ROS_ENV_ROOT}/jetson/ros2_ws/install/setup.bash"

if [[ "${sentinel_ros_env_had_u}" -eq 1 ]]; then
  set -u
fi
unset sentinel_ros_env_had_u SENTINEL_ROS_ENV_ROOT
