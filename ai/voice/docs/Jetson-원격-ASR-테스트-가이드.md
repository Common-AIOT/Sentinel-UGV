# Jetson 원격 ASR 상세 테스트 가이드

이 문서는 Jetson Orin Nano에서 다음 실제 경로를 처음부터 끝까지 검증하는 실행
가이드다.

```text
BRIO 100 마이크
  → Jetson 로컬 Silero VAD
  → GPU 개발 서버 Qwen3-ASR-1.7B
  → GMS gpt-5.4-mini 구조화
  → 기존 사전녹음 WAV 안내
  → /interaction/report
```

이번 단계의 목표는 **조용한 환경 기준선 1회를 정상 완료하는 것**이다. 이 기준선이
통과하기 전에 사이렌·모터 소음을 섞지 않는다. 기준선부터 실패하면 소음 성능과 설치
오류를 구분할 수 없다.

## 0. 검증에 사용하는 실제 경로

| 구분 | 경로 또는 값 |
|---|---|
| Jetson 사용자 | `orin` |
| Jetson 저장소 | `/home/orin/projects/S15P11A301` |
| Jetson Python | `/home/orin/projects/S15P11A301/.venv/bin/python` |
| Jetson STT 환경 파일 | `/home/orin/projects/S15P11A301/ai/voice/.env` |
| GPU 사용자 홈 | `/home/j-i15a301` |
| GPU ASR 저장소 | `/home/j-i15a301/a301-gpu-asr` |
| GPU ASR 실행기 | `/home/j-i15a301/a301-gpu-asr/run-asr.sh` |
| GPU ASR 런타임 환경 | `/home/j-i15a301/a301-gpu-asr/.env.runtime` |
| GPU ASR 로컬 포트 | `127.0.0.1:18100` |
| Jetson에서 시도할 주소 | `http://70.12.130.105:18100` |
| ROS 음성 입력 토픽 | `/perception/encounter` |
| ROS 음성 보고 토픽 | `/interaction/report` |
| ROS 대화 종료 토픽 | `/mission/signal` |

경로가 없을 때 비슷한 이름의 새 폴더를 만들지 않는다. 다음 명령으로 먼저 확인한다.

```bash
test -d /home/orin/projects/S15P11A301 && echo '[OK] Jetson repo'
test -x /home/orin/projects/S15P11A301/.venv/bin/python && echo '[OK] Jetson python'
```

둘 중 하나라도 출력되지 않으면 그 시점에서 중단하고 실제 clone 위치 또는 venv 위치를
확인한다.

## 1. MR 머지와 Jetson 코드 반영

MR !223이 `develop`에 머지된 뒤 Jetson 터미널에서 실행한다.

```bash
cd /home/orin/projects/S15P11A301
git status --short --branch
git switch develop
git pull --ff-only origin develop
git log -3 --oneline
```

`git status`에 수정 파일이 있으면 무조건 덮어쓰거나 삭제하지 않는다. 팀원이 만든 변경일
수 있으므로 먼저 내용을 확인한다. 정상이라면 다음 파일이 존재해야 한다.

```bash
test -f ai/voice/requirements-jetson-remote.txt
test -f scripts/jetson_voice_preflight.sh
test -f ai/voice/docs/Jetson-원격-ASR-테스트-가이드.md
```

세 명령의 종료 코드가 모두 0이어야 한다.

## 2. GPU 서버 확인과 외부 바인딩

이 절은 **GPU Jupyter 웹 터미널**에서 실행한다. Jetson 터미널과 혼동하지 않는다.

### 2-1. 기존 프로세스와 health 확인

```bash
pgrep -af '^/home/j-i15a301/a301-gpu-asr/.venv/bin/python -m asr_server$'
curl -sS http://127.0.0.1:18100/health
ss -ltnp | grep ':18100'
```

정상 health 예시는 다음과 같다.

```json
{
  "status": "ok",
  "ready": true,
  "backend": "qwen3-asr",
  "model": "Qwen/Qwen3-ASR-1.7B",
  "cuda_visible_devices": "3",
  "error_code": null
}
```

확인 기준:

- `ready`가 `true`여야 한다.
- 모델은 `Qwen/Qwen3-ASR-1.7B`여야 한다.
- `cuda_visible_devices`는 `3`이어야 한다.
- 다른 팀의 GPU 프로세스를 종료하지 않는다.

### 2-2. Jetson 직접 연결을 위한 단기 개발 바인딩

`ss` 결과가 `127.0.0.1:18100`이면 Jetson에서 접근할 수 없다. 단기 실기 검증 동안만
다음 파일을 연다.

```bash
nano /home/j-i15a301/a301-gpu-asr/.env.runtime
```

다음 줄을 추가하거나 기존 값을 수정한다.

```dotenv
ASR_HOST=0.0.0.0
```

`ASR_API_KEY`는 삭제하거나 빈 값으로 바꾸지 않는다. 파일 전체를 화면 공유하거나
Git에 올리지 않는다.

현재 ASR 프로세스가 정확히 하나인지 다시 확인한다.

```bash
ASR_PID="$(pgrep -f '^/home/j-i15a301/a301-gpu-asr/.venv/bin/python -m asr_server$')"
printf 'ASR_PID=%s\n' "$ASR_PID"
test -n "$ASR_PID"
test "$(printf '%s\n' "$ASR_PID" | wc -l)" -eq 1
```

마지막 두 명령이 성공했을 때만 그 프로세스를 재시작한다.

```bash
kill "$ASR_PID"
cd /home/j-i15a301/a301-gpu-asr
mkdir -p logs
nohup ./run-asr.sh > logs/asr.log 2>&1 &
sleep 5

curl -sS http://127.0.0.1:18100/health
ss -ltnp | grep ':18100'
tail -n 50 logs/asr.log
```

`ss`가 `0.0.0.0:18100` 또는 `*:18100`을 보여야 외부 연결을 시도할 수 있다. health가
`degraded`, `MODEL_LOAD_FAILED` 또는 연결 거부이면 Jetson으로 넘어가지 않는다.

> `0.0.0.0` 평문 바인딩은 기능 검증용이다. API 키와 음성이 암호화되지 않으므로
> 운영 유지 시 HTTPS reverse proxy 또는 승인된 사설 터널이 필요하다.

## 3. Jetson에서 GPU 서버 네트워크 확인

이 절부터는 **Jetson 터미널**에서 실행한다.

```bash
curl --connect-timeout 3 -sS http://70.12.130.105:18100/health
```

판정:

- `ready:true`: 다음 단계 진행
- `Connection refused`: GPU 서버가 여전히 loopback이거나 프로세스가 내려감
- `Connection timed out`: GPU 개발망이 Jetson의 18100 접근을 허용하지 않음
- HTML 로그인 화면: ASR API가 아니라 JupyterHub 웹 주소에 접속한 것

timeout이면 `.env`나 Python 패키지를 고쳐도 해결되지 않는다. GPU 가이드는 별도 SSH
접속점을 제공하지 않으므로 존재하지 않는 SSH 주소를 가정해 터널을 만들지 않는다.
교육망에서 Jetson → GPU 18100 허용 또는 정식 TLS endpoint 제공 여부를 확인해야 한다.

## 4. Jetson Python 환경 준비

```bash
cd /home/orin/projects/S15P11A301

.venv/bin/python -c "import platform; print(platform.machine())"
.venv/bin/python -c "import torch; print(torch.__version__, torch.cuda.is_available())"
```

기대값:

- 아키텍처: `aarch64`
- `torch.cuda.is_available()`: `True`

다음 파일은 원격 ASR 클라이언트용 경량 의존성만 설치한다. `faster-whisper`와
CTranslate2를 Jetson에 새로 설치하지 않는다.

```bash
.venv/bin/python -m pip install -r ai/voice/requirements-jetson-remote.txt

.venv/bin/python -c "import httpx, sounddevice, soundfile, silero_vad; print('[OK] voice deps')"
```

일반 PyPI `torch`를 새로 설치하지 않는다. JetPack과 맞는 기존 NVIDIA PyTorch를
유지해야 한다.

## 5. Jetson `.env` 작성

파일 위치는 `/home/orin/projects/S15P11A301/ai/voice/.env`다.

```bash
cd /home/orin/projects/S15P11A301/ai/voice
cp -n .env.example .env
chmod 600 .env
nano .env
```

`replace_with_team_gms_key`와 `replace_with_gpu_asr_api_key`만 실제 값으로 바꾼다.

- `GMS_KEY`: 팀에서 사용하던 GMS API 키
- `SENTINEL_ASR_API_KEY`: GPU 서버
  `/home/j-i15a301/a301-gpu-asr/.env.runtime`의 `ASR_API_KEY`와 같은 값

키 값을 출력하지 않고 설정 여부만 확인한다.

```bash
cd /home/orin/projects/S15P11A301/ai/voice
../../.venv/bin/python - <<'PY'
from sentinel_voice import config

assert config.ASR_API_KEY and not config.ASR_API_KEY.startswith("replace_")
assert config.GMS_KEY and not config.GMS_KEY.startswith("replace_")
print(config.summary())
print("[OK] ASR/GMS keys are configured")
PY
```

기대 설정 요약에는 다음 내용이 포함된다.

```text
jetson=True stt_backend=remote stt=Qwen/Qwen3-ASR-1.7B llm=gpt-5.4-mini
```

## 6. BRIO 100 마이크와 출력 장치 확인

BRIO 100과 기존 안내용 스피커를 연결한다.

```bash
arecord -l
pactl list short sources
pactl list short sinks
pactl info | grep -E 'Default Source|Default Sink'
```

`pactl list short sources`에서 BRIO 100에 해당하는 source의 **전체 이름**을 복사해
기본 입력으로 지정한다.

```bash
pactl set-default-source <BRIO 100 source 전체 이름>
pactl info | grep -i 'Default Source'
```

`<BRIO 100 source 전체 이름>`은 문자 그대로 입력하는 값이 아니다. 바로 앞 목록에서
확인한 실제 이름으로 교체한다.

3초 동안 평소 목소리로 말하며 입력만 검사한다.

```bash
cd /home/orin/projects/S15P11A301/ai/voice
../../.venv/bin/python -m tools.check_audio_io \
  --input-match pulse \
  --record-seconds 3 \
  --wav /home/orin/audio_io_sample.wav \
  --report /home/orin/audio_io_report.json
```

통과 기준:

- `[OK] 입력 장치` 출력
- WAV와 JSON 생성
- `peak`가 0이 아님
- `silent_input`이 `false`
- 클리핑 비율 `clipped_ratio`가 0에 가까움

전 구간 `peak 0`이면 조용한 것이 아니라 잘못된 PulseAudio source를 읽은 것이다.

## 7. 자동 preflight

GPU health, 인증된 ASR 요청, Silero VAD, GMS, 사전녹음 6개, 마이크, rclpy를 한 번에
검사한다.

```bash
cd /home/orin/projects/S15P11A301
./scripts/jetson_voice_preflight.sh
```

마지막에 다음 두 줄이 나와야 한다.

```text
[PASS] Jetson 음성 preflight 완료
증적: /home/orin/projects/S15P11A301/ai/voice/results/jetson-preflight/<UTC시각>
```

실패 위치별 의미:

| 실패 | 의미 |
|---|---|
| `ASR_UNAVAILABLE` | Jetson에서 GPU 주소에 도달하지 못함 |
| `UNAUTHORIZED` | Jetson과 GPU의 ASR API 키가 다름 |
| `MODEL_LOAD_FAILED` | GPU 모델이 ready 상태가 아님 |
| `GMS_KEY 미설정` | Jetson `.env`의 GMS 키 누락 |
| `VAD 로드 실패` | Jetson PyTorch 또는 Silero 설치 문제 |
| `peak 0` | PulseAudio 기본 source가 실제 BRIO가 아님 |
| `rclpy import` 실패 | venv에서 ROS 시스템 패키지가 보이지 않음 |

하나라도 실패하면 ROS E2E로 넘어가지 않는다.

## 8. ROS 2 환경 확인

```bash
cd /home/orin/projects/S15P11A301
test -f jetson/ros2_ws/install/setup.bash
source scripts/ros_env.sh
ros2 pkg prefix sentinel_bringup
ros2 node list
```

`install/setup.bash` 또는 `sentinel_bringup`이 없을 때만 워크스페이스를 빌드한다.

```bash
cd /home/orin/projects/S15P11A301/jetson/ros2_ws
source /opt/ros/humble/setup.bash
colcon build --symlink-install --packages-select sentinel_bringup
```

빌드 후 새 터미널에서 다시 `source scripts/ros_env.sh`를 실행한다.

개발 테스트 중 자동 실행 서비스와 수동 launch를 동시에 띄우지 않는다.

```bash
systemctl is-active sentinel-demo || true
```

`active`이면 먼저 다음으로 내린다.

```bash
sudo systemctl disable --now sentinel-demo
```

## 9. ROS 음성 노드 단독 기동

### 터미널 A - 음성 노드

```bash
cd /home/orin/projects/S15P11A301
source scripts/ros_env.sh
ros2 launch sentinel_bringup voice.launch.py repo_root:=/home/orin/projects/S15P11A301
```

기대 로그:

```text
voice_session 시작. APPROACHED 대기, 보고는 /interaction/report로 인계
```

프로세스가 바로 종료되면 `.venv`, `rclpy`, `ai/voice/.env`부터 다시 확인한다.

### 터미널 B - 결과 감시

```bash
cd /home/orin/projects/S15P11A301
source scripts/ros_env.sh

ros2 topic echo /interaction/report std_msgs/msg/String
```

이 터미널은 보고가 나올 때까지 그대로 둔다.

### 터미널 C - 임무 상태와 Encounter 수동 발행

```bash
cd /home/orin/projects/S15P11A301
source scripts/ros_env.sh
```

종료 안내가 탐사 재개 문구를 재생할 수 있도록 임무 상태를 먼저 보낸다.

```bash
ros2 topic pub --once /mission/status std_msgs/msg/String \
  "data: '{\"state\":\"EXPLORING\"}'"
```

같은 `encounterId`로 `CONFIRMED`를 먼저 보낸다.

```bash
ros2 topic pub --once /perception/encounter std_msgs/msg/String \
  "data: '{\"encounterId\":\"11111111-1111-4111-8111-111111111111\",\"phase\":\"CONFIRMED\",\"detectedAt\":\"2026-08-05T08:00:00Z\",\"personCount\":3,\"missionId\":\"22222222-2222-4222-8222-222222222222\",\"trackIds\":[1,2,3],\"confidence\":0.95,\"pose\":null}'"
```

그다음 같은 ID의 `APPROACHED`를 보낸다. 이 메시지가 음성 대화를 시작한다.

```bash
ros2 topic pub --once /perception/encounter std_msgs/msg/String \
  "data: '{\"encounterId\":\"11111111-1111-4111-8111-111111111111\",\"phase\":\"APPROACHED\",\"detectedAt\":\"2026-08-05T08:00:05Z\",\"personCount\":3,\"missionId\":\"22222222-2222-4222-8222-222222222222\",\"trackIds\":[1,2,3],\"confidence\":0.95,\"pose\":null}'"
```

`APPROACHED`만 먼저 보내면 상태 조정기가 거부하므로 대화가 시작되지 않는다.

## 10. 첫 기준선에서 말할 내용

조용한 실내에서 BRIO 정면 약 1m 위치에 한 명이 선다. 안내 WAV가 끝난 뒤 말한다.

| 순서 | 안내 질문 | 테스트 발화 예시 |
|---|---|---|
| 1 | 응답 확인 | `네, 여기 있어요.` |
| 2 | 긴급 상태 | `다리를 다쳐서 도움이 필요해요.` |
| 3 | 이동 가능 | `아니요, 다쳐서 움직일 수 없어요.` |
| 4 | 인원 | `저를 포함해서 세 명이에요.` |

안내 재생 중 겹쳐 말하지 않는다. 첫 기준선은 모델의 끼어들기 성능이 아니라 정상
파이프라인을 확인하는 시험이다.

터미널 A에서 다음 흐름을 확인한다.

```text
encounter CONFIRMED
encounter APPROACHED
각 질문의 VAD/STT/GMS 처리
음성 보고 인계: QUEUED
DIALOGUE_ENDED 발행
```

터미널 B의 `/interaction/report`에는 최소한 다음 판정이 기대된다.

```json
{
  "anyResponseDetected": true,
  "reportedResponsiveCount": 3,
  "mobilityStatus": "NO",
  "urgentConditionReported": "YES",
  "operatorReviewRequired": true,
  "terminationReason": "NORMAL"
}
```

필드 순서와 추가 envelope 필드는 달라도 된다. 위 핵심 값이 맞아야 한다.

## 11. 세션 증적 확인

새 터미널에서 실행한다.

```bash
cd /home/orin/projects/S15P11A301/ai/voice

LATEST_SESSION="$(find sessions -mindepth 1 -maxdepth 1 -type d \
  -printf '%T@ %p\n' | sort -nr | head -n 1 | cut -d' ' -f2-)"
printf 'LATEST_SESSION=%s\n' "$LATEST_SESSION"
find "$LATEST_SESSION" -maxdepth 1 -type f -printf '%f\n' | sort
```

최소 확인 파일:

- `session.jsonl`
- `report.json`
- 각 질문의 원본 WAV

내용을 확인한다.

```bash
sed -n '1,240p' "$LATEST_SESSION/session.jsonl"
/home/orin/projects/S15P11A301/.venv/bin/python -m json.tool \
  "$LATEST_SESSION/report.json"
```

각 턴에서 다음 계측값이 숫자로 남아야 한다.

- `recordMs`
- `vadMs`
- `sttMs`
- `gmsMs`
- `turnMs`

`safetyReason`이 있으면 해당 전사와 GMS 결과를 같이 확인한다.
`MOBILITY_YES_NEGATION_CONFLICT`는 부정어가 있는 전사를 GMS가 이동 가능으로 판정해
안전 가드가 `UNKNOWN`으로 낮췄다는 뜻이다.

## 12. 단독 기준선 통과 기준

다음 조건을 모두 만족해야 통과다.

- GPU health `ready:true`
- preflight 전체 PASS
- BRIO 입력 `silent_input:false`
- 사전녹음 안내 6개 중 필요한 안내가 정상 재생
- 네 질문의 실제 발화가 VAD를 통과
- 원격 ASR 호출에 timeout/401/503 없음
- `/interaction/report` 1건 발행
- `/mission/signal`의 `DIALOGUE_ENDED` 발행
- `terminationReason=NORMAL`
- 핵심 판정이 발화와 일치
- `session.jsonl`, `report.json`, 원본 WAV 저장

하나라도 어긋나면 그 기준선은 실패로 보존하고 원인을 고친 뒤 새 encounter ID로 다시
시도한다. 같은 encounter ID는 중복 세션 방지를 위해 다시 시작되지 않는다.

## 13. 전체 스택 통합 테스트

단독 음성 노드를 `Ctrl+C`로 내린 다음 잔류 프로세스를 확인한다.

```bash
pgrep -af 'sentinel_voice.ros_node' || true
```

잔류가 없을 때 전체 스택을 올린다. 첫 음성 통합 시험에서는 주행 안전을 위해 Nav2와
안전 구동 체인을 명시적으로 끈다.

```bash
cd /home/orin/projects/S15P11A301
./scripts/demo_up.sh enable_nav2:=false enable_safety:=false
```

별도 터미널에서 확인한다.

```bash
cd /home/orin/projects/S15P11A301
source scripts/ros_env.sh

ros2 node list | grep voice_session
ros2 topic info /perception/encounter
ros2 topic info /interaction/report
```

실제 detection/mission 흐름에서 사람이 확정되고 접근 상태가 되면 음성 세션이 한 번만
시작해야 한다. 테스트 종료는 다음 스크립트로 전체 스택을 내린다.

```bash
cd /home/orin/projects/S15P11A301
./scripts/demo_down.sh
```

`stop_sentinel.sh`는 센서·스트리밍만 내리므로 전체 스택 종료에 사용하지 않는다.

## 14. 자원 계측을 함께 실행할 때

단독 E2E가 통과한 뒤에만 전체 스택의 RAM·CPU·GPU를 측정한다.

```bash
cd /home/orin/projects/S15P11A301/ai/voice
../../.venv/bin/python -m evaluation.jetson_resource_bench \
  --label voice-remote-asr \
  --output-dir results/jetson-resource/voice-remote-asr \
  -- bash -lc 'cd /home/orin/projects/S15P11A301; timeout --signal=INT 180 ./scripts/demo_up.sh enable_nav2:=false enable_safety:=false; ./scripts/demo_down.sh'
```

명령 구간은 180초 뒤 SIGINT로 launch를 내리고 `demo_down.sh`가 잔류 프로세스를
정리한다. 전체 측정은 baseline 10초, 실행 180초, 종료 후 60초를 포함하므로 약
250초 걸린다. 명령이 중간에 끊겼다면 `demo_down.sh`를 다시 실행해 잔류를 확인한다.

## 15. 소음 시험으로 넘어가는 시점

다음 세 가지가 확보된 뒤에만 소음 시험을 시작한다.

1. 조용한 환경 단독 기준선 PASS
2. 전체 스택 통합 기준선 PASS
3. 두 기준선의 `session.jsonl`, `report.json`, WAV, 자원 로그 보존

그다음 같은 발화·거리·볼륨을 유지하고 조건만 `모터`, `사이렌`, `다중 화자`,
`거리`로 바꾼다. 소음 결과를 조용한 기준선과 비교해야 모델 문제, 마이크 문제,
네트워크 지연을 구분할 수 있다.

## 16. 빠른 장애 분류표

| 증상 | 먼저 볼 곳 | 조치 |
|---|---|---|
| Jetson curl timeout | GPU `ss -ltnp`, 교육망 ACL | Python 재설치 금지, 네트워크 경로부터 해결 |
| health degraded | GPU `logs/asr.log`, `nvidia-smi -i 3` | 모델 로드와 Device 3 확인 |
| 401/UNAUTHORIZED | 양쪽 ASR API 키 | 값을 로그에 출력하지 말고 동일 여부만 수정 |
| VAD가 전부 무응답 | `audio_io_report.json`, Pulse source | BRIO 기본 source 재지정 |
| ASR 전사가 비어 있음 | 저장된 질문 WAV | 음량·거리·마이크 경로 확인 |
| GMS만 실패 | GMS_KEY, GMS 도달성 | 키워드 폴백 사용 여부와 `usedFallback` 확인 |
| 보고가 없음 | missionId, `/interaction/report` 구독 시점 | 수동 JSON과 터미널 B 실행 순서 확인 |
| 대화가 시작 안 됨 | `/perception/encounter` 순서 | 같은 ID로 CONFIRMED 후 APPROACHED 발행 |
| 두 번 시작됨 | 중복 launch/process | systemd와 수동 launch 동시 실행 여부 확인 |
| 종료 안내가 없음 | `/mission/status` state | EXPLORING 상태를 세션 전에 발행 |
