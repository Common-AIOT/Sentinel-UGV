# 요구조자 음성 파이프라인

요구조자의 응답을 구조화해 관제에 전달하는 음성 서비스다. 현재 운영 경로는 다음 하나다.

```text
VISION Encounter
→ 승인된 사전 녹음 WAV
→ BRIO 100 입력 + Jetson Silero VAD
→ L40S FastAPI Qwen3-ASR-1.7B
→ GMS gpt-5.4-mini 구조화
→ 규칙 기반 위험도
→ 관제 보고 인계
```

설계, 안전 계약, 모델 선정 근거, GPU 서버와 Jetson 실행, 테스트 및 소음 실측은
루트 문서 [docs/08-AI-음성.md](../../docs/08-AI-음성.md)를 단일 기준으로 사용한다.

## 구성

| 경로 | 역할 |
|---|---|
| `sentinel_voice/` | 상태 머신, 오디오, 원격 ASR, GMS, 안전 규칙과 관제 인계 |
| `asr_server/` | L40S에서 Qwen3-ASR을 제공하는 인증 FastAPI 서버 |
| `assets/` | 승인된 사전 녹음 안내 WAV |
| `denoise/` | 관제 청취본 후처리. STT 입력에는 사용하지 않음 |
| `evaluation/` | ASR shadow, Jetson 자원과 파이프라인 평가 |
| `prompts/` | GMS 정보 추출 프롬프트 원문 |
| `tools/` | 환경·오디오·사전 녹음 자산 점검과 코퍼스 수집 |
| `tests/` | 하드웨어 없이 실행 가능한 계약·상태 머신 단위 테스트 |
| `docs/` | 실측 런북(전체 스택 RAM 프로브 등) |
| `results/` | 측정 집계 요약. 원음·전사 원문은 넣지 않는다 |

가중치, 개인 음성, 원본 전사, `.env`와 API 키는 커밋하지 않는다. `results/`에는
사람 음성이 포함되지 않은 집계 요약(JSON·CSV)만 커밋한다.

## 환경 설정

```bash
cd ai/voice
cp .env.example .env
```

`.env`에서 `GMS_KEY`, `SENTINEL_ASR_BASE_URL`, `SENTINEL_ASR_API_KEY`를 실제 팀 값으로
교체한다. 키는 코드·문서·터미널 캡처에 노출하지 않는다.

## 테스트

```bash
cd ai/voice
python -m unittest discover -s tests -v
python -m pytest tests/test_integration.py -v
python -m tools.validate_guide_assets
python -m tools.check_env
```

`tests/test_integration.py`만 pytest 형식(모듈 레벨 test 함수·`parametrize`)이라
`unittest discover`가 수집하지 못한다. 두 명령을 모두 실행해야 전체가 돈다.

## GPU ASR 서버

```bash
cd ai/voice
python3.12 -m venv .venv-gpu-asr
source .venv-gpu-asr/bin/activate
python -m pip install -U pip
python -m pip install -r asr_server/requirements.txt
cp asr_server/.env.example .env.gpu-asr
set -a
source .env.gpu-asr
set +a
python -m asr_server
```

```bash
curl -sS http://127.0.0.1:18100/health
```

기본 bind는 `127.0.0.1`이다. Jetson 직접 연결을 위한 격리 개발망 시험에서만
`0.0.0.0`과 평문 HTTP 예외를 사용하고, 장기 운영은 TLS 또는 승인된 터널 뒤에 둔다.

## GPU 서버와 Jetson 통합 실행 Runbook

아래 절차는 팀원이 그대로 복사해 GPU ASR 서버를 시작하고 Jetson 음성 파이프라인을
시험할 수 있는 실행 기준이다. 비밀 키는 문서나 Git에 기록하지 않는다.

### 1. GPU 서버 코드와 GPU 확인

GPU 서버 기준 경로는 `/home/j-i15a301/a301-gpu-asr`이다. 실제 서버 모듈은
`gpu_server`가 아니라 `asr_server`다.

```bash
nvidia-smi -i 3

cd /home/j-i15a301/a301-gpu-asr
ls -l asr_server/__main__.py
```

`asr_server/__main__.py`가 없다면 임의의 모듈명으로 실행하지 말고 실제 배치 위치를 찾는다.

```bash
find /home/j-i15a301 -maxdepth 6 \
  -type f \
  -path '*/asr_server/__main__.py' \
  -print
```

검색된 `asr_server`의 바로 상위 디렉터리에서 이후 명령을 실행한다.

### 2. GPU 서버 런타임 환경 설정

최초 한 번 API 키를 생성한다.

```bash
openssl rand -hex 32
```

출력값은 GPU 서버의 `ASR_API_KEY`와 Jetson의 `SENTINEL_ASR_API_KEY`에 동일하게
입력한다. 키는 터미널 캡처, 메신저, Git에 노출하지 않는다.

```bash
nano /home/j-i15a301/a301-gpu-asr/.env.runtime
```

```dotenv
ASR_API_KEY=<공유하지 않는 ASR 비밀 키>
ASR_MODEL_ID=Qwen/Qwen3-ASR-1.7B
ASR_CUDA_VISIBLE_DEVICES=3
ASR_DTYPE=bfloat16
ASR_HOST=0.0.0.0
ASR_PORT=18100
ASR_MAX_CONCURRENCY=1
ASR_MAX_INFERENCE_BATCH_SIZE=1
```

`ASR_BACKEND=qwen`은 현재 서버가 읽지 않는 이전 설정이므로 넣지 않는다. 파일 권한을
제한한다.

```bash
chmod 600 /home/j-i15a301/a301-gpu-asr/.env.runtime
```

`.env.runtime`은 파일만 만들어서는 적용되지 않는다. 서버를 시작하는 셸에서 반드시
`source`한다.

### 3. GPU ASR 서버 실행

```bash
cd /home/j-i15a301/a301-gpu-asr

set -a
source .env.runtime
set +a

/home/j-i15a301/a301-gpu-asr/.venv/bin/python \
  -u -m asr_server
```

서버 터미널은 Jetson 시험이 끝날 때까지 유지한다. `0.0.0.0`은 외부 요청을 받기 위한
bind 주소이며 Jetson이 요청할 목적지 주소가 아니다.

GPU 서버의 새 터미널에서 상태를 확인한다.

```bash
curl -sS http://127.0.0.1:18100/health
ss -ltnp | grep ':18100'
nvidia-smi -i 3
```

정상 `/health` 응답의 핵심 값은 다음과 같다.

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

`ss` 결과는 Jetson 직접 연결 시험에서 `0.0.0.0:18100`을 보여야 한다. bind 설정은
방화벽을 열지 않으므로 연결이 거부되면 GPU 서버 방화벽과 네트워크 ACL의 TCP 18100
허용 여부도 확인한다. 평문 HTTP와 외부 bind는 격리된 개발망에서만 사용한다.

### 4. Jetson 코드와 가상환경 준비

관련 MR이 `develop`에 병합된 다음 Jetson에서 실행한다.

```bash
cd /home/orin/projects/S15P11A301
git status
git pull --ff-only origin develop
```

가상환경이 이미 있으면 재생성하지 않는다.

```bash
ls -l /home/orin/projects/S15P11A301/.venv/bin/python
```

가상환경이 없는 경우에만 다음을 실행한다.

```bash
cd /home/orin/projects/S15P11A301
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
python -m pip install -r ai/voice/requirements-jetson-remote.txt
```

### 5. Jetson 환경 설정

Jetson 음성 모듈은 `/home/orin/projects/S15P11A301/ai/voice/.env`를 자동으로 읽는다.

```bash
nano /home/orin/projects/S15P11A301/ai/voice/.env
```

```dotenv
GMS_KEY=<팀 GMS 키>
SENTINEL_LLM=gpt-5.4-mini

SENTINEL_ASR_BASE_URL=http://70.12.130.105:18100
SENTINEL_ASR_API_KEY=<GPU 서버 ASR_API_KEY와 동일한 값>
SENTINEL_ASR_ALLOW_INSECURE_HTTP=1
SENTINEL_ASR_TIMEOUT=8
SENTINEL_ASR_CONNECT_TIMEOUT=2
SENTINEL_ASR_MAX_ATTEMPTS=2
SENTINEL_ASR_RETRY_DELAY=0.2

SENTINEL_SESSION_LOG_DIR=sessions
```

```bash
chmod 600 /home/orin/projects/S15P11A301/ai/voice/.env
```

Python 실행에는 별도 `source .env`가 필요 없다. 아래처럼 셸의 `curl`에서 환경변수를
사용할 때만 직접 불러온다.

### 6. Jetson에서 GPU 서버 연결과 인증 확인

먼저 공개 health 경로를 확인한다.

```bash
curl -sS http://70.12.130.105:18100/health
```

이어서 인증과 WAV 업로드 경로까지 확인한다.

```bash
cd /home/orin/projects/S15P11A301/ai/voice

set -a
source .env
set +a

curl -sS http://70.12.130.105:18100/v1/asr \
  -H "Authorization: Bearer $SENTINEL_ASR_API_KEY" \
  -H "X-Request-ID: jetson-smoke-001" \
  -F "language=ko" \
  -F "audio=@assets/guide_intro.wav;type=audio/wav"
```

`curl: (26)`이면 WAV 파일 경로를, `401`이면 두 서버의 ASR API 키 일치 여부를 확인한다.

### 7. Jetson 오디오 장치 확인

```bash
arecord -l
aplay -l
pactl list sources short
pactl list sinks short
```

기본 마이크로 5초 녹음하고 재생한다.

```bash
arecord -f S16_LE -r 16000 -c 1 -d 5 /tmp/brio-test.wav
aplay /tmp/brio-test.wav
```

BRIO가 아닌 장치가 녹음되면 실제 source 이름을 확인해 기본 입력을 변경한다.

```bash
pactl get-default-source
pactl set-default-source <실제 BRIO source 이름>
```

사전 녹음 안내도 실제 출력 장치로 재생되는지 확인한다.

```bash
aplay /home/orin/projects/S15P11A301/ai/voice/assets/guide_intro.wav
```

### 8. 사전 점검과 단독 파이프라인 실행

```bash
cd /home/orin/projects/S15P11A301/ai/voice

/home/orin/projects/S15P11A301/.venv/bin/python \
  -m tools.check_env

/home/orin/projects/S15P11A301/.venv/bin/python \
  -m tools.check_env --load

/home/orin/projects/S15P11A301/.venv/bin/python \
  -m tools.validate_guide_assets
```

`FAIL`을 모두 해결한 뒤 파이프라인을 실행한다.

```bash
cd /home/orin/projects/S15P11A301/ai/voice

/home/orin/projects/S15P11A301/.venv/bin/python \
  -u -m sentinel_voice.pipeline
```

안내 음성이 나오면 실제 마이크를 향해 각 질문에 답한다. 정상 실행은 `[PLAY]`, `[STT]`,
음성 세션 보고, 위험도 참고값, 종료 상태를 출력한다.

단독 실행에서 아래 결과는 오류가 아니다.

```text
관제 보고 상태: PENDING (관제 전송 어댑터 미연결)
```

단독 파이프라인은 STT·LLM·위험도 판단을 확인하지만 ROS/MQTT 관제 어댑터를 연결하지
않는다. 실제 관제 전송은 다음 ROS 실행으로 확인한다.

### 9. ROS 음성 노드와 전체 파이프라인 실행

최신 ROS 패키지를 빌드하고 환경을 불러온다.

```bash
source /opt/ros/humble/setup.bash
cd /home/orin/projects/S15P11A301/jetson/ros2_ws
colcon build --symlink-install
source install/setup.bash
```

음성 노드만 실행할 때:

```bash
ros2 launch sentinel_bringup voice.launch.py \
  repo_root:=/home/orin/projects/S15P11A301
```

전체 로봇 파이프라인을 실행할 때:

```bash
ros2 launch sentinel_bringup demo.launch.py
```

음성 노드는 `/perception/encounter`의 `APPROACHED`를 받아 세션을 시작하고, 결과를
`/interaction/report`로, 종료 안내 재생을 마친 뒤 `DIALOGUE_ENDED`를
`/mission/signal`로 발행한다. 진행 중 세션은 `/mission/status`의 ESTOP·ERROR·
MANUAL·PAUSED에서 중단한다. 별도 터미널에서 확인할 수 있다.

```bash
source /opt/ros/humble/setup.bash
source /home/orin/projects/S15P11A301/jetson/ros2_ws/install/setup.bash
ros2 topic echo /interaction/report
```

Cloud Bridge는 이 보고를 MQTT `INTERACTION_REPORT`로 관제에 인계한다. 유효한
`missionId`가 없는 보고는 관제로 전송하지 않는다.

### 10. 결과 수집

```bash
cd /home/orin/projects/S15P11A301/ai/voice

LATEST_SESSION=$(find sessions -mindepth 1 -maxdepth 1 -type d | sort | tail -n 1)
echo "$LATEST_SESSION"
cat "$LATEST_SESSION/report.json"
cat "$LATEST_SESSION/session.jsonl"
```

시험 담당자는 다음 자료를 공유한다.

- GPU 서버 `/health` 응답
- Jetson `tools.check_env --load` 결과
- 파이프라인 전체 터미널 로그
- 최신 `report.json`과 `session.jsonl`
- 실제 발화와 잘못 인식된 STT 결과
- 실제 발화와 잘못 분류된 LLM 결과
- 마이크·스피커 정상 여부
- 연결 거부, 인증 실패, 타임아웃 여부

### 11. 빠른 실행 요약

GPU 서버:

```bash
cd /home/j-i15a301/a301-gpu-asr
set -a
source .env.runtime
set +a
/home/j-i15a301/a301-gpu-asr/.venv/bin/python -u -m asr_server
```

Jetson:

```bash
curl -sS http://70.12.130.105:18100/health

cd /home/orin/projects/S15P11A301/ai/voice
/home/orin/projects/S15P11A301/.venv/bin/python -m tools.check_env --load
/home/orin/projects/S15P11A301/.venv/bin/python -u -m sentinel_voice.pipeline
```

설계 근거, 모델 선정, 안전 계약, 정량 측정과 소음 시험 기준은
[docs/08-AI-음성.md](../../docs/08-AI-음성.md)를 따른다.

## 요구조자 위치와 추가 인원 제보

ROS Encounter의 `pose`는 최종 보고의 `encounterPose`로 전달한다. 이는 요구조자의
정밀 좌표가 아니라 로봇이 음성 상호작용을 수행한 위치다.

COUNT 답변에 추가 사람과 위치가 이미 포함되어 있으면 `additionalPersonReports`로
보존한다. 예를 들어 “2층에 우리 아기가 있어요”는 대상, 추가 인원 1명, 원문 위치
`2층`과 정규화된 `reportedFloor=2`로 전달한다. 존재만 언급된 사람의 응답 상태는
`UNKNOWN`이고 모든 제보는 `UNVERIFIED` 및 관제 확인 필요 상태다.

“저기 계단 옆에 있어요”처럼 사람의 존재와 위치만 있고 숫자가 없으면 인원 수를
추정하지 않는다. 이때 `reportedCount=null`,
`countStatus=PRESENCE_CONFIRMED_COUNT_UNKNOWN`으로 보존한다. 현재 SLAM 지도에는
“계단” 같은 장소 의미가 없으므로 모든 제보의 `groundingStatus`는 `UNGROUNDED`다.

- 위치를 되묻는 추가 질문과 동적 TTS를 사용하지 않는다.
- 원문에 없는 장소·좌표를 생성하지 않는다.
- 추가 인원 수를 `reportedResponsiveCount`에 임의로 합치지 않는다.
- 음성 제보로 로봇 목표지나 주행 경로를 자동 변경하지 않는다.

## 현재 제한

- 동적 TTS를 사용하지 않는다.
- Jetson에 Whisper 또는 로컬 LLM을 올리지 않는다.
- DeepFilterNet 결과를 STT 입력으로 사용하지 않는다.
- 사투리와 다국어는 현재 검증 범위가 아니다.
- 전체 로봇 스택과 소음 환경 성능은 실제 Jetson에서 계속 측정한다.

## 소음 강건성 회귀 측정

원본 음성과 소음은 수정하지 않고 저장소 밖의 출력 폴더에 파생본만 만든다. 다음 예시는
직접 녹음한 도메인 발화 16개와 수집한 소음 WAV 6종을 SNR 10/5/0dB로 합성한다.

```powershell
cd ai/voice
python -m evaluation.noise_corpus `
  --manifest C:\Users\SSAFY\audio-test\corpus\manifest.jsonl `
  --noise-dir C:\Users\SSAFY\audio-test\data\noise `
  --output-dir C:\Users\SSAFY\audio-test\domain-noise-bench-303

python -m evaluation.pipeline_audio_corpus `
  --manifest C:\Users\SSAFY\audio-test\domain-noise-bench-303\manifest.jsonl `
  --output-dir C:\Users\SSAFY\audio-test\domain-noise-bench-303\pipeline-16k

python -m evaluation.vad_noise_bench `
  --manifest C:\Users\SSAFY\audio-test\domain-noise-bench-303\pipeline-16k\manifest.jsonl `
  --output C:\Users\SSAFY\audio-test\domain-noise-bench-303\pipeline-16k\vad-summary.json
```

생성 결과는 clean 16건, `16발화 × 6소음 × 3 SNR` 288건, 소음 전용 6건으로 총
310건이다. `pipeline_audio_corpus`는 실제 Voice 입력과 같은 16kHz mono/RMS 정규화를
적용한다. `noise-components`와 `speech-components`에는 각 혼합본에 사용된 정확한 성분을
따로 저장하므로 사람이 원음·소음·혼합본을 대조 청취할 수 있다. 오디오와 전체 전사 결과는
개인 음성 데이터이므로 저장소에 커밋하지 않는다.

S15P11A301-303에서 기존 실제 세션을 재분석해 RMS 0.002998의 `네`, 0.004041의
`없습니다`가 과거 0.005 선게이트에 막힌 것을 확인했다. 기본
`SENTINEL_SILENCE_RMS=0.001`은 이 약한 발화를 Silero VAD까지 보내기 위한 값이다.
디지털 무음과 VAD 판정은 별도 단계로 계속 적용한다.

운영 형식의 실제 육성 기준 Qwen은 310/310 요청에 성공했고 P95는 408.31ms였다. 반면
clean CER도 17.74%였고 SNR 10/5/0dB CER는 30.11%/36.69%/45.03%로, 현재 수치만으로
재난 현장 강건성을 주장할 수 없다. 특히 `realmotor`와 `moto`가 취약했다. 위험 발화를
안전으로 뒤집은 사례와 소음 전용 6건의 ASR 환각은 없었다. Silero는 음성 304/304를
감지했지만 소음 전용 3/6을 음성으로 오탐했으므로 유지하되 후속 BRIO 실측에서 재검증한다.

과거 5개 합성 발화 기반 CER 0.89% 결과는 평가 도구의 예비 회귀 시험일 뿐이며 주 성능
근거로 사용하지 않는다. 원격 Qwen은 지연과 안전 극성 보존을 근거로 잠정 유지하지만,
화자 3명 이상과 BRIO 동시 녹음으로 최종 승인해야 한다. DeepFilterNet은 기존 108조건
A/B에서 정확도를 낮췄으므로 STT 전처리로 추가하지 않는다.

질문 문맥 기반 GMS의 보완 효과는 토큰을 제한해 대표 24건만 측정했다. 운영
`gpt-5.4-mini` 프롬프트의 최종 슬롯 정확도는 11/24(45.83%)로 키워드 폴백
7/24(29.17%)보다 4건 높았다. 그러나 `moto@5dB` 이동 불가 발화를 이동 가능으로 확정한
위험→안전 반전이 1건 있었다. 따라서 LLM은 제한적 오류 복원 계층으로만 사용하며 소음
대책으로 표현하지 않는다. 재현 도구는 `evaluation.llm_noise_recovery_bench`이고 상세
결과 원문은 개인 평가 폴더에 보존한다.
