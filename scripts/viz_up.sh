#!/usr/bin/env bash
#
# Foxglove 시각화를 켠다 (S15P11A301-216).
#
#   ./scripts/viz_up.sh                 # LAN(0.0.0.0) — 노트북에서 본다
#   ./scripts/viz_up.sh --local         # 127.0.0.1 만 — SSH 터널로 본다
#   ./scripts/viz_up.sh viz_port:=9000  # 인자는 viz.launch.py 로 그대로 전달된다
#
# **돌고 있는 스택에 붙는다.** demo_up.sh 를 다시 띄우지 않는다. SLAM 지도는
# 메모리에만 있고 임무 종료 시점에 저장되므로(S15P11A301-171), 스택을 재시작하면
# 그때까지 그린 지도를 잃는다.
#
# 백그라운드로 띄운다. `ros2 launch` 는 포그라운드를 잡아서 그대로 실행하면
# 터미널이 묶이고, 그래서 실제로는 매번 손으로 nohup 을 붙이고 있었다.
#
# 끄는 것은 ./scripts/viz_down.sh 다. S15P11A301-224 에서 읽기 전용으로 좁혔다
# (capabilities=[connectionGraph], 토픽 6개 화이트리스트) — 파라미터 변경·서비스
# 호출은 이제 안 된다. 다만 **읽기는 인증 없이 열려 있다.** 젯슨이 공인 IP 에
# 있어 LAN 과 인터넷이 같은 인터페이스이고 "LAN 에서만 열기" 라는 선택지가 없다.
# 그래서 볼 때만 켜고 보고 나면 끈다.
#
# 같은 224 에서 TLS 도 켰다. **주소는 wss:// 다.** 아래 안내 문구는 실제
# viz_tls 값에서 계산한다 — 문자열로 박아 두면 기본값이 바뀔 때 안내만 낡고,
# 그러면 "안내대로 했는데 안 붙는다" 가 된다(실제로 그렇게 됐다).
set -Eeuo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_FILE="${SENTINEL_VIZ_LOG:-/tmp/sentinel-viz.log}"
DEFAULT_PORT=8765
DEFAULT_ADDRESS=0.0.0.0
WAIT_SECONDS=30

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  cat <<'USAGE'
Usage: ./scripts/viz_up.sh [--local] [launch 인자...]

  --local   127.0.0.1 로만 연다. 외부에 노출되지 않고 SSH 터널로 본다.
            생략하면 0.0.0.0 이며 같은 망의 노트북에서 바로 접속한다.

돌고 있는 스택에 Foxglove Bridge 를 붙인다. 스택을 재시작하지 않는다.
끄기: ./scripts/viz_down.sh
USAGE
  exit 0
fi

address="${DEFAULT_ADDRESS}"
local_only=0
if [[ "${1:-}" == "--local" ]]; then
  address=127.0.0.1
  local_only=1
  shift
fi

# 호출자가 직접 viz_address·viz_port·viz_tls 를 준 경우 그것을 존중한다. 여기서
# 덮으면 인자를 줬는데 왜 안 먹느냐가 되고, 그 원인은 로그만 봐서는 알 수 없다.
address_given=0
tls_given=0
port="${DEFAULT_PORT}"
tls=true   # viz.launch.py 의 viz_tls 기본값과 같아야 한다
for arg in "$@"; do
  case "${arg}" in
    viz_address:=*) address_given=1; address="${arg#viz_address:=}" ;;
    viz_port:=*) port="${arg#viz_port:=}" ;;
    viz_tls:=*) tls_given=1; tls="${arg#viz_tls:=}" ;;
  esac
done

# SSH 터널로 볼 때는 TLS 를 끈다. 터널이 이미 암호화하고 있어 두 겹으로 쌀
# 이유가 없고, 무엇보다 **인증서 이름이 안 맞는다** — jetson.sentinel-ugv.xyz
# 로 발급된 인증서를 wss://localhost 로 검증하면 브라우저가 거부한다.
if [[ "${local_only}" -eq 1 && "${tls_given}" -eq 0 ]]; then
  tls=false
fi

# 안내에 쓸 스킴. 문자열을 두 군데 박지 않는다.
scheme=ws
if [[ "${tls,,}" == "true" ]]; then
  scheme=wss
fi

# 포트가 실제로 열렸는지 본다. "띄웠다"만 출력하고 끝내면 실패해도 성공처럼
# 보인다 — 이 프로젝트에서 반복해서 겪은 형태다.
port_listening() {
  command -v ss >/dev/null 2>&1 || return 1
  ss -tln 2>/dev/null | awk -v pattern=":${1}\$" '$4 ~ pattern { found = 1 } END { exit !found }'
}

if port_listening "${port}"; then
  echo "이미 켜져 있습니다 — ${port} 포트가 열려 있습니다."
  echo "  두 번째를 띄우면 포트 바인딩에 실패하고 로그만 헷갈려지므로 그냥 둡니다."
  echo "  로그: ${LOG_FILE}"
  echo "  끄기: ./scripts/viz_down.sh"
  exit 0
fi

# ROS 소싱과 DDS 격리 설정(S15P11A301-218). 스택과 같은 값이어야 bridge 가
# 우리 노드를 본다 — 다르면 Foxglove 에 토픽이 하나도 안 뜨고 그 이유가
# 어디에도 남지 않는다.
# shellcheck source=ros_env.sh disable=SC1091
source "${REPO_ROOT}/scripts/ros_env.sh"

launch_args=("$@")
if [[ "${address_given}" -eq 0 ]]; then
  launch_args+=("viz_address:=${address}")
fi
if [[ "${tls_given}" -eq 0 ]]; then
  launch_args+=("viz_tls:=${tls}")
fi

# setsid 로 세션을 끊는다. 부모 셸이 닫혀도 살아 있어야 하고, 이 스크립트를
# 부른 터미널의 Ctrl-C 가 전달되지 않아야 한다.
setsid nohup ros2 launch sentinel_bringup viz.launch.py "${launch_args[@]}" \
  >"${LOG_FILE}" 2>&1 &

echo "Foxglove Bridge 를 띄웁니다 (${address}:${port})…"
for _ in $(seq "${WAIT_SECONDS}"); do
  if port_listening "${port}"; then
    break
  fi
  sleep 1
done

if ! port_listening "${port}"; then
  echo "실패: ${WAIT_SECONDS}초 안에 ${port} 가 열리지 않았습니다." >&2
  echo "로그 마지막 20줄:" >&2
  tail -20 "${LOG_FILE}" >&2 || true
  exit 1
fi

echo "켜졌습니다."
echo
if [[ "${local_only}" -eq 1 ]]; then
  echo "  접속 전에 노트북에서 터널을 엽니다:"
  echo "    ssh -N -L ${port}:127.0.0.1:${port} orin@<젯슨 주소>"
  echo "  그다음 Foxglove 에서  ${scheme}://localhost:${port}"
else
  echo "  Foxglove 에서  ${scheme}://jetson.sentinel-ugv.xyz:${port}"
  # 젯슨에서 직접 볼 때는 이름이 아니라 localhost 라서 인증서 이름이 안 맞는다.
  # TLS 를 켠 채로는 브라우저가 거부하므로 그 경우는 안내하지 않는다.
  if [[ "${scheme}" == "ws" ]]; then
    echo "  젯슨에서 직접 보면  ws://localhost:${port}"
  fi
fi
if [[ "${scheme}" == "wss" ]]; then
  echo "  wss 다. ws 로 붙으면 서버가 TLS 핸드셰이크를 기다리다 끊고,"
  echo "  Foxglove 에는 'No details provided' 만 남는다."
fi
echo "  연결 유형은 반드시 'Foxglove WebSocket' 입니다. Rosbridge 를 고르면"
echo "  핸드셰이크가 깨집니다."
echo
echo "  3D 패널: 톱니 → Display frame 을 map 으로 바꾸고 /map, /scan 을 켭니다."
echo "  Display frame 이 기본값이면 지도가 로봇과 함께 돌아 읽을 수 없습니다."
echo
if [[ "${local_only}" -eq 0 ]]; then
  echo "  주의: ${port} 는 읽기에 인증이 없습니다. 쓰기는 막혀 있지만"
  echo "  (S15P11A301-224: 읽기 전용, 토픽 6개) 지도·영상 프레임·TF 는 누구나"
  echo "  받아 갑니다. 젯슨이 공인 IP 라 외부에서도 닿습니다. 보고 나면 끄십시오."
fi
echo "  로그: ${LOG_FILE}"
echo "  끄기: ./scripts/viz_down.sh"
