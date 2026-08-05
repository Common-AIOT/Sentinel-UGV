#!/usr/bin/env bash
# Jetson 음성 파이프라인을 실제 세션 전에 한 번에 점검한다.
# API 키와 녹음 내용은 콘솔에 출력하지 않는다.
set -Eeuo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${REPO_ROOT}/.venv/bin/python"
VOICE_ROOT="${REPO_ROOT}/ai/voice"
ENV_FILE="${VOICE_ROOT}/.env"
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
RESULT_DIR="${VOICE_ROOT}/results/jetson-preflight/${RUN_ID}"

if [[ ! -f /etc/nv_tegra_release ]]; then
  echo "[FAIL] NVIDIA Jetson에서만 실행한다: /etc/nv_tegra_release 없음" >&2
  exit 1
fi
if [[ ! -x "${PYTHON}" ]]; then
  echo "[FAIL] ${PYTHON} 없음. 저장소 루트 .venv를 먼저 만든다." >&2
  exit 1
fi
if [[ ! -f "${ENV_FILE}" ]]; then
  echo "[FAIL] ${ENV_FILE} 없음. .env.example을 복사한 뒤 실제 키를 넣는다." >&2
  exit 1
fi

mkdir -p "${RESULT_DIR}"

# ROS 2 시스템 패키지와 저장소 overlay, DDS 격리를 동일하게 적용한다.
# shellcheck source=ros_env.sh disable=SC1091
source "${REPO_ROOT}/scripts/ros_env.sh"
cd "${VOICE_ROOT}"

"${PYTHON}" - <<'PY'
from sentinel_voice import config

if not config.ASR_API_KEY:
    raise SystemExit("[FAIL] SENTINEL_ASR_API_KEY가 없다")
print(f"[OK] 설정: {config.summary()}")
PY

# 실제 GPU 서버 health, Bearer 인증 전사, VAD 로드, GMS 호출까지 확인한다.
"${PYTHON}" -m tools.check_env --load
"${PYTHON}" -m tools.validate_guide_assets

# PulseAudio 기본 입력으로 3초를 녹음해 죽은 입력(전 구간 peak 0)을 잡는다.
"${PYTHON}" -m tools.check_audio_io \
  --input-match pulse \
  --record-seconds 3 \
  --wav "${RESULT_DIR}/microphone.wav" \
  --report "${RESULT_DIR}/audio-report.json"

# launch가 사용하는 venv Python에서 ROS 2 모듈이 보이는지도 확인한다.
"${PYTHON}" -c "import rclpy; print('[OK] rclpy import')"

echo "[PASS] Jetson 음성 preflight 완료"
echo "증적: ${RESULT_DIR}"
echo "다음: ros2 launch sentinel_bringup voice.launch.py repo_root:=${REPO_ROOT}"
