# 요구조자 음성 파이프라인

요구조자의 음성을 듣고 → 상태 정보를 구조화 → 관제 보고 상태에 맞는 안전
안내 음성을 내보내는 **음성 파이프라인**입니다. VAD는 Jetson 로컬, STT는
인증된 FastAPI GPU ASR 서버에서 수행하며,
정보 구조화(LLM)는 **GMS API 호출**, 안내 음성은 **사전녹음 재생**으로 동작합니다.

```
트리거(VISION) → 네트워크 확인 ─↘ 오프라인: 세션 미시작 (안내 없음, 로그만)
              → 다턴 대화 세션 (INTRO → URGENT → MOBILITY → COUNT → CLOSING)
                  각 단계: 안내음성 재생 → 청취 → VAD → STT → 환각 가드 → GMS 추출
                                                  ↘ GMS만 실패: 33-8 키워드 폴백
                  값 미확정이어도 되묻지 않고 UNKNOWN으로 진행
              → 규칙 위험도 → 관제 보고 인계 → 종료 안내 1회
```

무응답·STT 실패·해석 실패·정상 응답을 4분류로 구분하며, **STT 실패를 무응답으로 기록하지
않습니다**(명세 33-3). 질문 순서와 실패 규칙은 `conversation.py`, 실물 입출력 연결은
`session_runner.py`가 담당합니다.

## 확정된 스택 (측정·팀 결정 근거)

| 단계 | 선택 | 근거 |
|------|------|------|
| VAD | Silero VAD (로컬) | 노이즈 1차 컷, torch 기반 경량 |
| STT | **Qwen3-ASR-1.7B** (L40S FastAPI 서버) | Jetson의 CTranslate2·모델 메모리를 제거. 원격 장애는 무응답이 아닌 STT 실패로 분류 |
| LLM | **`gpt-5.4-mini`** (GMS API) | Jira 118 프롬프트 v2 실측 44/44 완전 정답으로 선정. 로컬 3b는 젯슨 피크 5.62GB·OOM([근거](docs/measurements/메모리-예산.md)) |
| LLM 폴백 | 키워드 파서(`llm.keyword_extract`) | STT 완료 후 GMS 호출만 실패한 경우의 축소 보고 |
| 안내 음성 | **승인된 사전녹음 WAV 재생**(`assets/`) | TTS 모델 미탑재로 RAM 절약. 형식 검사는 `python -m tools.validate_guide_assets` |
| 등급 | 규칙(`safety.triage_rule`) | LLM 자유판단 배제, 재현·설명 가능 |

## 폴더 구성

| 경로 | 역할 |
|------|------|
| `sentinel_voice/` | 실제 음성 서비스 패키지. 설정·오디오·안전 규칙·GMS·파이프라인 |
| `sentinel_voice/config.py` | 원격 ASR·GMS·VAD·오디오 파라미터와 `.env` 로드 |
| `sentinel_voice/audio.py` | 오디오 로더·레벨 처리(16kHz mono float32 통일, 무음 판정용 원본 RMS) |
| `sentinel_voice/safety.py` | STT 환각 가드, LLM 출력 보정, 규칙 기반 triage |
| `sentinel_voice/llm.py` | GMS 호출 + 33-8 키워드 폴백 (`extract()` 단일 진입점) |
| `sentinel_voice/gms_resilience.py` | GMS 장애 분류·제한 재시도·호스트 도달성 검사 |
| `sentinel_voice/session_gate.py` | 신규 STT 세션 시작 전 GMS 가용성 게이트 |
| `sentinel_voice/encounter.py` | 비전 Encounter 계약 검증과 encounter당 음성 세션 1회 조정 |
| `sentinel_voice/report_delivery.py` | 관제 전송 대기/대기열 인계 상태 계약 |
| `sentinel_voice/pipeline.py` | 엔드투엔드 실행(다턴 대화 세션 조립·보고). 모델은 첫 사용 시 지연 로딩 |
| `sentinel_voice/conversation.py` | 다턴 상태머신(질문 4개, 부상 우선 순서)과 VAD·STT·구조화 결과 4분류. 재질문은 INTRO 무응답 1회뿐 |
| `sentinel_voice/session_runner.py` | 상태머신에 실제 마이크·STT·GMS·안내 음성을 연결하는 어댑터 |
| `sentinel_voice/remote_asr.py` | GPU ASR 인증·타임아웃·제한 재시도 클라이언트. 원음·키를 로그에 남기지 않음 |
| `sentinel_voice/guide_audio.py` | 승인 문구 목록, WAV 형식 검사, 안전 재생 결과 |
| `tools/` | 배포 전 환경·오디오·사전녹음 자산 점검 |
| `evaluation/` | 측정용 다회차 벤치(지연·일관성) |
| `tests/` | 하드웨어 없이 실행 가능한 상태머신·안전 규칙 단위 테스트 |
| `prompts/` | 정보 추출 프롬프트(진단 금지, 사실만) |
| `docs/README.md` | **단일 기준 문서** — 설계·안전 정책·보고 계약·실행 절차·테스트·검증 기준·용어집 |
| `docs/measurements/` | 원본 측정 기록 (RAM 실측, GMS 모델 비교 표) — 재현 근거 보존용 |

AI·음성 담당이 아닌 팀원은 세부 문서를 읽기 전에
[`docs/README.md` §13 용어집](docs/README.md)에서
약어, 평가 지표, 보고 필드와 장애 처리 용어를 확인할 수 있습니다.
오디오 검사 코드가 수집하는 항목과 산출물은
[`docs/README.md` §7-4](docs/README.md)에 설명합니다.
실제 장비의 합격 여부는 별도 검증 절차로 판정합니다.
사전녹음 파일 목록과 제작·청취·에코 검증은
[`docs/README.md` §6 안내 음성 자산](docs/README.md)를 따릅니다.

가중치·녹음 데이터·`results/`·**`.env`(GMS 키)**는 커밋하지 않습니다(`.gitignore`).

상태머신 단위 테스트:

```bash
cd ai/voice
python -m unittest discover -s tests -v
```

## GMS 설정 (필수)

```bash
# ai/voice/.env 생성 후 GMS_KEY를 실제 팀 키로 교체
cp .env.example .env       # Linux/Jetson
# copy .env.example .env   # Windows Miniforge Prompt
```

> GMS Key는 팀 크레딧과 연결된 비밀 값입니다. 코드·문서·커밋에 절대 넣지 마세요.
> GMS 키는 장기적으로 관제 백엔드의 환경 변수 또는 비밀 저장소에서 관리합니다.
> Jetson 직접 호출을 사용하는 개발 단계에서는 `ai/voice/.env`에만 두며 커밋하지 않습니다.
> 네트워크 단절이 확인되면 신규 STT 대화를 시작하지 않습니다. 이미 STT가 완료된 뒤
> GMS 호출만 실패한 경우에 한해 `llm.py`의 33-8 키워드 폴백을 사용합니다.

## GPU ASR 운영 경로

STT 운영 경로는 FastAPI 원격 ASR 하나입니다. GPU 서버를 TLS reverse proxy 또는
승인된 사설 경로 뒤에 준비하고 API 키를 안전하게 전달한 뒤 다음 값을 `.env`에 넣습니다.

```dotenv
SENTINEL_ASR_BASE_URL=https://asr.example.internal
SENTINEL_ASR_API_KEY=replace_with_random_asr_key
SENTINEL_ASR_TIMEOUT=8
SENTINEL_ASR_CONNECT_TIMEOUT=2
SENTINEL_ASR_MAX_ATTEMPTS=2
```

- loopback 외 평문 HTTP는 기본 거부합니다. 개발망에서 불가피할 때만
  `SENTINEL_ASR_ALLOW_INSECURE_HTTP=1`을 명시합니다.
- 429·503·timeout·전송 오류는 안정된 오류 코드로 바뀌며, VAD가 이미 사람 음성을
  찾은 턴은 `VOICE_DETECTED_STT_FAILED`로 남습니다. `NO_RESPONSE`로 바뀌지 않습니다.
- Qwen3-ASR에는 Whisper의 `no_speech_prob`이 없으므로 로컬 VAD를 통과한 비어 있지
  않은 전사만 0.0으로 호환 매핑하고, 빈 전사는 1.0으로 처리합니다.
- API 키와 원음은 원격 클라이언트 로그에 남기지 않습니다.
- 운영 ASR 서버는 선정이 완료된 Qwen3-ASR만 로드합니다. Whisper 비교 결과는
  `results/asr-shadow-l40s-20260805/`에 근거로만 남겨 둡니다.

## 음성 세션 보고 스키마

```json
{
  "responseScope": "GROUP",
  "anyResponseDetected": true,
  "reportedResponsiveCount": 2,
  "reportedCountStatus": "SELF_REPORTED_GROUP_COUNT",
  "countConfidence": null,
  "mobilityStatus": "NO",
  "urgentConditionReported": "UNKNOWN",
  "operatorReviewRequired": true,
  "terminationReason": "NORMAL"
}
```

필드별 한글 의미, `null`과 `UNKNOWN`의 차이, 결정 주체와 오류 예시는
[`docs/README.md` §3 보고 계약](docs/README.md)를 따릅니다.
관제 보고 생성부터 Closing 재생과 후속 MQTT·ROS 2 연결 경계는
[`docs/README.md` §5-2](docs/README.md)에 용어와 사례를 포함해 설명합니다.
**관제 ACK는 없습니다** — 로봇이 여러 대 투입되어 관제가 개별 보고에 ACK를 내리지
않습니다(2026-08-01 확정). 종료 안내는 발신 상태와 무관하게 한 번만 재생합니다.
비전 Encounter의 `CONFIRMED`·`APPROACHED` 사건과 음성 세션 시작·중단 조건은
[`docs/README.md` §5-1](docs/README.md)를 따릅니다.

GMS와 키워드 폴백은 인원 수·이동 가능 여부·긴급 상태 언급만 추출합니다.
응답 감지 여부와 종료 사유는 VAD·상태머신이 결정합니다.

색상 등급은 최종 구조 우선순위가 아니라 관제 검토용 참고값입니다. 로봇은 이를 요구조자에게
직접 안내하지 않으며, 구조 ETA는 관제가 제공한 유효한 값만 승인된 템플릿으로 전달합니다.
세부 계약은 [`docs/README.md` §2 안전 정책](docs/README.md)를 따릅니다.

---

## Jetson Orin Nano 배포

원격 L40S Qwen3-ASR 구성으로 Jetson을 준비할 때는 먼저
[`docs/Jetson-원격-ASR-실행.md`](docs/Jetson-원격-ASR-실행.md)의 실제 연결 확인,
경량 의존성 설치, 자동 preflight, ROS 2 단독 E2E 순서를 따른다. 소음 벤치는 이
preflight와 단독 E2E가 통과한 뒤 실행한다.

Jetson 앞에서 명령을 한 줄씩 실행하는 실기 절차와 기대 결과, 수동 Encounter
트리거, 증적 확인, 장애별 중단 기준은
[`docs/Jetson-원격-ASR-테스트-가이드.md`](docs/Jetson-원격-ASR-테스트-가이드.md)에
정리한다.

x86과 ARM64의 차이는 이제 로컬 STT 엔진이 아니라 Jetson용 PyTorch(Silero VAD)와
오디오 장치 설정에만 영향을 줍니다. Jetson에는
`requirements-jetson-remote.txt`만 설치하며 CTranslate2를 설치하지 않습니다.

Silero VAD는 첫 로드 시 가중치를 내려받을 수 있으므로 온라인 상태에서 preflight를
한 번 수행해 캐시를 채웁니다.

```bash
cd ai/voice
python -m tools.check_env --load  # 원격 ASR 인증 + VAD 로드 + GMS 실호출 점검
```

> 안내 음성은 사전녹음 WAV(`assets/`, 저장소에 포함)라 캐싱이 필요 없고, LLM(GMS)은 온라인 전용
> — 네트워크 단절이 확인되면 신규 STT 대화는 시작하지 않는다. 33-8 폴백은
> STT 완료 후 GMS 호출만 실패한 경우에 사용한다.

> 로컬 캐시는 `~/.cache/torch/hub`의 VAD 가중치뿐입니다. Qwen3-ASR 가중치는 GPU
> 서버에서 관리합니다.

### STEP 2 — 코드 이식 & 점검

```bash
# 이 저장소를 젯슨에 clone 후
cd ai/voice
python -m tools.check_env          # 원격 ASR 설정/오디오/GMS/스왑 점검
python -m tools.check_env --load   # 원격 ASR 인증 전사/VAD 실제 로드까지 확인
```

`python -m tools.check_env`가 전부 `[OK]`면 STT 구동 준비 완료입니다.
`[FAIL]`부터 해결하세요.

BRIO 100 마이크와 Bluetooth 스피커의 실제 장치 선택·녹음·재생은
[`docs/README.md` §7-4](docs/README.md)의 절차로 별도 검증합니다.

### STEP 3 — 실행

```bash
python -m sentinel_voice.pipeline  # 1=마이크 8초, 2=파일 / 트리거는 VISION 기본
```

### STEP 4 — Jetson 메모리 절약 전략

1. **STT 원격화** — Qwen3-ASR 모델과 추론 메모리는 L40S 서버가 담당하고 Jetson에는
   HTTP 클라이언트만 둡니다.
2. **안내 음성 사전녹음** — ✅ 적용됨. 문구 v2 WAV 6개 생성·변환·형식 검증 완료(2026-08-03). 블루투스 청취 검수는 실기에서.
3. **LLM 미탑재** — ✅ 적용됨. GMS API 호출로 젯슨 LLM RAM 0 (구 로컬 3b는 피크 5.62GB였음).

### STEP 5 — 측정 (보고서 핵심 수치)

`jtop`을 켠 채로 벤치를 돌려 기록합니다.

```bash
python -m evaluation.pipeline_bench   # results/pipeline_bench_summary.csv
```

| 항목 | 방법 | 목표 |
|------|------|------|
| RAM 점유 | jtop MEM | 8GB 안에서 여유 |
| STT/LLM/E2E 지연 | 벤치 로그 | 실용 범위인가 |
| GPU/CPU/온도/전력 | jtop / tegrastats | 스로틀링 없나 |
| ⭐ **비전(YOLO)+SLAM 동시 구동** | 팀원과 통합 | **진짜 시험** — OOM·FPS 저하 |

> 가장 중요한 측정은 마지막 줄입니다. GMS 전환으로 오디오 예상 피크가 ~3.3GB로 줄었지만
> (LLM·STT 미탑재), 통합 실측으로 확인하기 전까지는 여유를 단정하지 않습니다.

> 📄 RAM 예산과 실측 기록은 [`docs/measurements/메모리-예산.md`](docs/measurements/메모리-예산.md)에 정리합니다(팀 공유용).
> 예상 예산표 + 젯슨에서 채우는 실측 템플릿이 들어 있습니다.
> Jetson 자원 측정은 [`docs/README.md` §9-6](docs/README.md)의 자동 로거
> (`evaluation/jetson_resource_bench.py`)로 baseline·실행 중 peak·종료 60초 후 값을 함께 남깁니다.
> 실행 명령과 단계별 통과 기준도 같은 절에 있습니다.
> 전체 STT·GMS·E2E·자원 통과 기준과 공통 결과 필드는
> [`docs/README.md` §10 정량 검증 기준](docs/README.md)를 따릅니다.

### GMS 모델 선정 결과

후보 모델 비교는 완료됐으며 운영 모델은 `gpt-5.4-mini`다.
상세 지표와 미채택 근거는
[`docs/measurements/GMS-모델-비교-결과.md`](docs/measurements/GMS-모델-비교-결과.md)에 보존한다.

L40S의 `Qwen/Qwen3.5-4B` 로컬 shadow 서버와 동일 46케이스 비교 결과는
[`docs/measurements/Qwen3.5-4B-로컬-shadow.md`](docs/measurements/Qwen3.5-4B-로컬-shadow.md)에
기록했습니다. 안전 치명 오분류 1건과 P50 7.58초 때문에 운영 전환하지 않고
`gpt-5.4-mini` GMS 경로를 유지합니다.

---

## 환경 변수 (config 오버라이드)

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `SENTINEL_ASR_BASE_URL` | 127.0.0.1:18100 | 원격 ASR 기본 URL. loopback 외 HTTPS 필수 |
| `SENTINEL_ASR_API_KEY` | (없음, remote 필수) | GPU ASR 인증 키 — `.env`로만 관리 |
| `SENTINEL_ASR_TIMEOUT` | 8 | 전사 요청 총 제한 시간(초) |
| `SENTINEL_ASR_CONNECT_TIMEOUT` | 2 | 연결 제한 시간(초) |
| `SENTINEL_ASR_MAX_ATTEMPTS` | 2 | 일시 장애 최대 시도 횟수 |
| `SENTINEL_LLM` | gpt-5.4-mini | GMS 모델명 |
| `GMS_KEY` | (없음, **필수**) | GMS API 키 — `ai/voice/.env`로 관리, 커밋 금지 |
| `SENTINEL_GMS_BASE` | gms.ssafy.io/…/v1 | GMS OpenAI 호환 엔드포인트 |
| `SENTINEL_LLM_TIMEOUT` | 10 | STT 완료 후 GMS 호출 시간 초과 시 33-8 키워드 폴백 |
| `SENTINEL_GMS_MAX_ATTEMPTS` | 2 | 최초 호출을 포함한 최대 GMS 호출 횟수 |
| `SENTINEL_GMS_RETRY_DELAY` | 0.5 | 일시 장애 재시도 전 대기 시간(초) |
| `SENTINEL_GMS_PROBE_TIMEOUT` | 2 | 신규 세션 전 GMS 호스트 연결 확인 제한 시간(초) |

GMS 장애 분류와 관제 전송 대기 상태는
[`docs/README.md` §5-3](docs/README.md)를 따릅니다.
관제 ACK와 탐사 재개 Closing 규칙은
[`docs/README.md` §5-2](docs/README.md)를 따릅니다.

```bat
python -m tools.check_gms_resilience
python -m tools.check_gms_resilience --live --report results\gms-smoke.json
```

첫 명령은 외부 API를 호출하지 않으며, 두 번째 명령만 고정 합성 문장으로 GMS를
실호출합니다. 두 명령 모두 키나 인증 헤더를 출력하지 않습니다.

## 개발 PC(x86)에서 테스트

```bash
pip install -r requirements.txt   # 젯슨과 달리 x86은 이대로 설치 가능(torch는 별도)
python -m tools.check_env
```

## GPU ASR 후보 shadow 벤치마크

Qwen3-ASR, faster-whisper large-v3, large-v3-turbo를 동일 음성으로 비교하는 도구는
`evaluation/asr_shadow_bench.py`다. 한국어·영어 합성 스모크 코퍼스와 무음·잡음 케이스,
L40S 실측 결과, 현장 코퍼스 승격 기준은
[`docs/ASR-shadow-벤치마크.md`](docs/ASR-shadow-벤치마크.md)에 정리했다.

```bash
ASR_API_KEY=... python -m evaluation.asr_shadow_bench \
  --manifest evaluation/fixtures/asr-shadow/manifest.jsonl \
  --endpoint qwen=http://127.0.0.1:18100 \
  --runs 3
```

## 알려진 이슈

- **사투리 미지원:** 현재 검증·합격 범위는 표준 한국어와 일반 구어체다. 사투리
  인식 성능은 측정하지 않으며 최종 STT 점수에도 포함하지 않는다.
- **근-침묵 환각**: 완전 무음이 아닌 매우 작은 잔향에서 STT가 헛단어를 뱉는 경우가 관측됨.
  현재 `SILENCE_RMS`(원본 RMS 게이트) + `is_valid_stt`(반복/무음확률/프롬프트복사 컷)로 방어하나
  완전하지 않음. 개선 방향: 블록리스트 + 침묵 재녹음 재측정 + STT 프롬프트에서 특정 단어 제거.
- `evaluation/`는 `data/` 샘플이 있어야 의미 있음(없으면 해당 시나리오 `NO_FILE` 스킵). 녹음은 개발 PC에서 준비.
