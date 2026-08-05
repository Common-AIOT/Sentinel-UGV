# Jetson 원격 ASR 실행 절차

이 절차의 목표는 Jetson에서 **로컬 Silero VAD → L40S Qwen3-ASR → GMS 구조화 →
기존 사전녹음 WAV** 경로를 먼저 정상 기동하는 것이다. 소음 성능 측정은 이 절차가
통과한 뒤 별도 조건으로 수행한다.

## 1. 현재 확인된 경로

- Jetson 저장소: `~/projects/S15P11A301`
- Jetson 음성 코드: `~/projects/S15P11A301/ai/voice`
- Jetson Python: `~/projects/S15P11A301/.venv/bin/python`
- GPU 서버 코드: `/home/j-i15a301/a301-gpu-asr/ai/voice/asr_server`
- GPU 서버 실행기: `/home/j-i15a301/a301-gpu-asr/run-asr.sh`
- GPU 서버 로컬 health: `http://127.0.0.1:18100/health`
- 모델: `Qwen/Qwen3-ASR-1.7B`, 물리 GPU `3`

GPU 개발 서버 가이드는 VPN 뒤 JupyterHub와 웹 터미널 사용만 안내하며 SSH 접속점을
제공하지 않는다. 따라서 존재하지 않는 SSH 주소로 터널을 만들지 않는다. Jetson에서
`70.12.130.105:18100`에 직접 도달할 수 있는지는 아래 3단계에서 실측해 결정한다.

## 2. Jetson 코드와 런타임 준비

Jetson 터미널에서 실행한다.

```bash
cd ~/projects/S15P11A301
git switch develop
git pull --ff-only origin develop

test -x .venv/bin/python
.venv/bin/python -c "import torch; print(torch.__version__, torch.cuda.is_available())"
.venv/bin/python -m pip install -r ai/voice/requirements-jetson-remote.txt
```

마지막 출력의 CUDA 값은 `True`여야 한다. 이 requirements 파일은 원격 모드에 필요
없는 `faster-whisper`, CTranslate2, FastAPI 서버 패키지를 Jetson에 설치하지 않는다.
NVIDIA Jetson용 PyTorch는 기존 설치를 그대로 사용한다.

## 3. GPU 서버를 Jetson에서 볼 수 있는지 확인

GPU Jupyter 터미널에서 먼저 실행한다.

```bash
curl -sS http://127.0.0.1:18100/health
ss -ltnp | grep ':18100'
```

health의 `ready`가 `true`여야 한다. `ss` 결과가 `127.0.0.1:18100`이면 외부 Jetson은
접속할 수 없다. 단기 실기 검증에서만 `/home/j-i15a301/a301-gpu-asr/.env.runtime`의
`ASR_HOST`를 `0.0.0.0`으로 설정하고 `/home/j-i15a301/a301-gpu-asr/run-asr.sh`로
재기동한다. API 키 없는 공개 실행은 금지한다.

Jetson 터미널에서 다음을 실행한다.

```bash
curl --connect-timeout 3 -sS http://70.12.130.105:18100/health
```

- JSON과 `ready:true`가 나오면 단기 검증 경로로 사용할 수 있다.
- timeout 또는 connection refused이면 코드 문제가 아니라 GPU 개발망의 접근 경로
  문제다. 가이드에 SSH 접속점이 없으므로 임의 SSH 터널을 만들지 말고, 교육망에서
  Jetson의 해당 포트 접근 허용 또는 TLS reverse proxy 제공 여부를 확인해야 한다.
- 평문 HTTP는 API 키와 요구조자 음성을 암호화하지 않는다. 기능 검증이 끝난 뒤 운영
  유지 여부를 판단할 때는 반드시 TLS endpoint 또는 승인된 사설 터널로 전환한다.

## 4. Jetson `.env` 설정

파일은 정확히 `~/projects/S15P11A301/ai/voice/.env`다. 저장소에 커밋하지 않는다.

```bash
cd ~/projects/S15P11A301/ai/voice
cp -n .env.example .env
chmod 600 .env
nano .env
```

단기 직접 연결이 성공한 경우 다음 항목을 넣는다.

```dotenv
SENTINEL_ASR_BASE_URL=http://70.12.130.105:18100
SENTINEL_ASR_API_KEY=<GPU 서버 /home/j-i15a301/a301-gpu-asr/.env.runtime의 ASR_API_KEY와 같은 값>
SENTINEL_ASR_ALLOW_INSECURE_HTTP=1
SENTINEL_ASR_TIMEOUT=8
SENTINEL_ASR_CONNECT_TIMEOUT=2
SENTINEL_ASR_MAX_ATTEMPTS=2
SENTINEL_ASR_MODEL_LABEL=Qwen/Qwen3-ASR-1.7B
GMS_KEY=<팀 GMS API 키>
SENTINEL_LLM=gpt-5.4-mini
```

`<...>` 부분은 설명용 자리표시자이며 그대로 입력하는 값이 아니다. API 키를 확인할
때 `cat .env.runtime` 전체를 화면 공유하거나 로그에 남기지 않는다.

## 5. 자동 preflight

BRIO 100을 연결하고 PulseAudio 기본 입력을 BRIO로 맞춘 다음 실행한다.

```bash
pactl list short sources
pactl set-default-source <위 목록의 BRIO 100 source 이름>
pactl info | grep -i 'Default Source'

cd ~/projects/S15P11A301
./scripts/jetson_voice_preflight.sh
```

스크립트는 다음을 한 번에 검사한다.

1. Jetson과 저장소 `.venv`, `.env`
2. 원격 ASR `/health`
3. 같은 키로 인증된 `/v1/asr` 요청
4. Silero VAD 실제 로드
5. GMS 실제 호출
6. 승인된 사전녹음 WAV 6개
7. PulseAudio 마이크 3초 녹음과 디지털 무음 여부
8. venv에서 `rclpy` import

녹음 증적은
`~/projects/S15P11A301/ai/voice/results/jetson-preflight/<UTC시각>/`에 저장된다.

## 6. 음성 노드 단독 E2E

터미널 1:

```bash
cd ~/projects/S15P11A301
source scripts/ros_env.sh
ros2 launch sentinel_bringup voice.launch.py repo_root:=$PWD
```

터미널 2:

```bash
cd ~/projects/S15P11A301
source scripts/ros_env.sh
ros2 topic echo /interaction/report std_msgs/msg/String
```

비전 노드와의 실제 세션은 `/perception/encounter`에서 같은 encounter ID의
`CONFIRMED`를 받은 뒤 이어지는 `APPROACHED` 이벤트가 시작한다. 복사 가능한 수동
트리거와 결과 판독은
[`Jetson-원격-ASR-테스트-가이드.md`](Jetson-원격-ASR-테스트-가이드.md)를 따른다.
단독 음성 확인 후 전체 스택은 다음으로 올린다.

```bash
cd ~/projects/S15P11A301
./scripts/demo_up.sh
```

각 턴의 `recordMs`, `vadMs`, `sttMs`, `gmsMs`, `turnMs`와 안전 가드 사유는
`ai/voice/sessions/<세션시각>/session.jsonl`에 남는다. 이 정상 동작을 확인한 뒤 같은
장비·거리·음량을 유지하면서 조용함, 모터, 사이렌, 다중 화자 조건을 측정한다.
