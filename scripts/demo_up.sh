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

SERVICE_NAME=sentinel-demo.service

# ----------------------------------------------------------------------
# 이중 기동 거부 (S15P11A301-294)
#
# 서비스가 이미 스택을 돌리고 있는데 손으로 이것을 부르면 두 벌이 뜬다. 카메라
# 단일 오픈(32-3)이 깨지고 MediaMTX 가 같은 경로에 두 번 바인딩한다. 그때 증상은
# "안 뜬다"가 아니라 **영상이 간헐적으로 끊기는 것**이라 원인을 찾기 어렵다
# (S15P11A301-125 가 stream_pipeline 중복으로 그것을 겪었다).
#
# 서비스가 부르는 경우는 제외해야 한다. 그때 이 스크립트의 부모가 systemd 이고,
# ExecStart 시점에는 유닛이 이미 activating 이므로 아래 검사에 걸린다.
# INVOCATION_ID 는 systemd 가 자기 자식에게만 주는 환경변수다.
# ----------------------------------------------------------------------
if [[ -z "${INVOCATION_ID:-}" ]] \
   && systemctl is-active --quiet "${SERVICE_NAME}" 2>/dev/null; then
  echo "거부: ${SERVICE_NAME} 가 이미 스택을 돌리고 있습니다." >&2
  echo "  두 벌이 뜨면 카메라 단일 오픈(32-3)이 깨지고 영상이 간헐적으로 끊깁니다." >&2
  echo "  로그를 보려면 : journalctl -u sentinel-demo -f" >&2
  echo "  다시 올리려면 : sudo systemctl restart sentinel-demo" >&2
  echo "  손으로 올리려면: sudo systemctl stop sentinel-demo 뒤에 이것을 부릅니다." >&2
  exit 1
fi

# ----------------------------------------------------------------------
# 어느 코드로 뜨는지 남긴다 (S15P11A301-294)
#
# 이 워크스페이스는 symlink(egg-link) 설치라 `install/` 이 소스를 직접 가리킨다.
# 즉 **체크아웃돼 있는 브랜치가 곧 로봇이 실행하는 코드다.** 서비스가 enabled 인
# 동안에는 부팅만으로 스택이 뜨므로, 기능 브랜치를 체크아웃한 채 재부팅하면
# 로봇이 경고 없이 그 코드로 뜬다. 2026-08-05 저녁에 실제로 그 상태였다.
#
# 막지는 않는다 — 브랜치에서 실기동 검증을 하는 것은 정당한 사용이다. 대신
# journalctl 에 남겨 사후에 "그날 무슨 코드였나"를 답할 수 있게 한다.
# ----------------------------------------------------------------------
if git -C "${REPO_ROOT}" rev-parse --git-dir >/dev/null 2>&1; then
  branch="$(git -C "${REPO_ROOT}" branch --show-current 2>/dev/null || true)"
  commit="$(git -C "${REPO_ROOT}" rev-parse --short HEAD 2>/dev/null || true)"
  dirty=""
  if [[ -n "$(git -C "${REPO_ROOT}" status --porcelain 2>/dev/null)" ]]; then
    dirty=" +미커밋변경"
  fi
  echo "코드: ${branch:-<detached>} @ ${commit:-?}${dirty}"
  if [[ "${branch}" != "develop" || -n "${dirty}" ]]; then
    echo "  경고: develop 의 깨끗한 상태가 아닙니다. 이 스택은 위 코드로 돕니다." >&2
    echo "        시연·검증 결과를 이 커밋과 함께 기록하십시오." >&2
  fi
fi

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

# 마이크 입력 소스 고정 (S15P11A301-330).
#
# PulseAudio 기본 소스가 젯슨 온보드 아날로그 입력(alsa_input.platform-sound)으로
# 잡히면 아무것도 꽂혀 있지 않으므로 **디지털 무음**이 나온다. 2026-08-07 E2E
# 리허설이 그 상태였고 대가가 둘이었다.
#
#   음성 인식   요구조자가 대답했는데 무응답으로 판정하고 세션이 즉시 끝났다
#   증빙 오디오 이벤트 영상의 오디오 트랙이 mean_volume -91.0 dB (완전 무음)
#
# 두 소비자가 같은 기본 소스를 쓴다 — voice 의 PortAudio 경로와 stream_pipeline
# 의 pulsesrc 다. 그래서 한 곳에서 고친다. 증빙 오디오는 명세 32-6 요구사항이라
# 무음이면 요구조자와의 대화가 남지 않는다.
#
# 여기서 하는 이유: demo_up.sh 가 스택의 단일 진입점(S15P11A301-294)이고 모든
# 노드의 부모다. 데스크톱 세션의 pulse 설정에 의존하지 않고 매 기동마다
# 강제되며, PULSE_SOURCE 를 export 하면 두 소비자가 함께 상속한다.
#
# 시리얼이 아니라 모델 부분열로 찾는다. 같은 모델의 다른 유닛으로 바꿔도 듣는다.
#
# 못 찾아도 스택을 막지 않는다(32장 장애 격리) — 다만 무엇이 조용해지는지 적는다.
# "파일은 만들어지고 오디오 트랙도 있는데 소리만 없는" 상태가 겉으로는 성공처럼
# 보이기 때문이다.
: "${XDG_RUNTIME_DIR:=/run/user/$(id -u)}"
export XDG_RUNTIME_DIR
MIC_MATCH=usb-046d_Brio_100
if ! command -v pactl >/dev/null 2>&1; then
  echo "마이크: pactl 이 없어 기본 소스를 확인하지 못한다." \
       "음성 인식과 증빙 영상 오디오가 무음일 수 있다(S15P11A301-330)." >&2
else
  MIC_SOURCE="$(pactl list short sources 2>/dev/null |
    awk -v m="${MIC_MATCH}" '$2 ~ m && $2 !~ /monitor/ {print $2; exit}')"
  if [[ -n "${MIC_SOURCE}" ]]; then
    pactl set-default-source "${MIC_SOURCE}" >/dev/null 2>&1 || true
    export PULSE_SOURCE="${MIC_SOURCE}"
    echo "마이크: ${MIC_SOURCE} 로 고정(PULSE_SOURCE)."
  else
    echo "마이크: ${MIC_MATCH} 소스를 못 찾았다. 기본 소스는" \
         "'$(pactl info 2>/dev/null | sed -n 's/^Default Source: //p')' 다." >&2
    echo "        온보드 입력이면 음성 인식이 무응답으로 오판하고 증빙 영상" \
         "오디오가 무음이 된다(명세 32-6). USB 연결을 확인하라." >&2
  fi
fi

exec ros2 launch sentinel_bringup demo.launch.py "$@"
