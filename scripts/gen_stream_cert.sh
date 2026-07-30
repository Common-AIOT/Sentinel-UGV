#!/usr/bin/env bash
#
# 로컬 WebRTC용 자체 서명 인증서 생성 (S15P11A301-106)
#
# EC2에서 HTTPS로 열린 관제 페이지가 Jetson의 평문 HTTP 신호 주소에 접근하면
# 브라우저가 혼합 콘텐츠로 차단한다. 그래서 Jetson의 WHEP 엔드포인트도
# HTTPS여야 한다(명세 32-4).
#
# 기본 경로는 공인 인증서다(S15P11A301-145). jetson.sentinel-ugv.xyz 의
# Let's Encrypt 인증서를 DNS-01로 발급해 같은 위치(server.crt/server.key)에
# 두면 어느 기기에서도 신뢰 등록 없이 접속된다. 발급·갱신 절차는
# jetson/ros2_ws/src/sentinel_streaming/README.md 의 「공인 인증서」 절.
#
# 이 스크립트는 인터넷이 없는 환경의 **폴백**이다. 자체 서명 인증서를 만들고
# 관제 노트북이 이를 신뢰하도록 등록한다. 신뢰 등록은 사람이 해야 한다.
#
# 인증서와 키는 git에 커밋하지 않는다. .gitignore로 제외한다.
#
# 사용법:
#   ./scripts/gen_stream_cert.sh [출력디렉터리]
#
# 주의: ROS setup.bash와 무관하므로 set -u를 써도 된다.
set -Eeuo pipefail

OUT_DIR="${1:-${HOME}/.config/sentinel/certs}"
DAYS="${DAYS:-825}"   # 브라우저가 825일을 초과하는 인증서를 거부한다
CN="${CN:-sentinel.local}"

mkdir -p "${OUT_DIR}"
KEY="${OUT_DIR}/server.key"
CRT="${OUT_DIR}/server.crt"

if [[ -s "${KEY}" && -s "${CRT}" ]]; then
  echo "인증서가 이미 있다: ${CRT}"
  openssl x509 -in "${CRT}" -noout -subject -dates -ext subjectAltName
  echo
  echo "다시 만들려면 위 두 파일을 지우고 실행한다."
  exit 0
fi

# SAN에 호스트명과 현재 IP를 모두 넣는다. 브라우저는 CN이 아니라 SAN을 본다.
host_ips=$(hostname -I 2>/dev/null | tr ' ' '\n' | grep -E '^[0-9]+\.' || true)
san="DNS:${CN},DNS:localhost,DNS:$(hostname),IP:127.0.0.1"
for ip in ${host_ips}; do
  san="${san},IP:${ip}"
done

echo "인증서 생성"
echo "  CN  : ${CN}"
echo "  SAN : ${san}"
echo "  기간: ${DAYS}일"

openssl req -x509 -newkey rsa:2048 -sha256 -nodes \
  -keyout "${KEY}" -out "${CRT}" -days "${DAYS}" \
  -subj "/CN=${CN}" \
  -addext "subjectAltName=${san}" \
  -addext "basicConstraints=critical,CA:FALSE" \
  -addext "keyUsage=critical,digitalSignature,keyEncipherment" \
  -addext "extendedKeyUsage=serverAuth" 2>/dev/null

chmod 600 "${KEY}"
chmod 644 "${CRT}"

echo
echo "생성 완료:"
echo "  ${CRT}"
echo "  ${KEY}"
echo
openssl x509 -in "${CRT}" -noout -subject -dates -ext subjectAltName
echo
echo "다음 단계:"
echo "  1. MediaMTX에 인증서를 지정한다."
echo "       ros2 launch sentinel_streaming streaming.launch.py \\"
echo "         webrtc_encryption:=true \\"
echo "         webrtc_cert:=${CRT} webrtc_key:=${KEY}"
echo "  2. 관제 노트북이 이 인증서를 신뢰하도록 등록한다(사람이 해야 함)."
echo "       ${CRT} 를 노트북으로 복사한 뒤 OS 신뢰 저장소에 추가한다."
echo "  3. 브라우저에서 https://${CN}:8889/sentinel/whep 로 접속한다."
echo
echo "신뢰 등록을 하지 않으면 브라우저가 WHEP 요청을 차단한다."
