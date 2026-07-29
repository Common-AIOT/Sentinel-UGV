# STT 음성 파이프라인

요구조자의 음성을 듣고 → 상태 정보를 구조화 → 관제 보고 상태에 맞는 안전
안내 음성을 내보내는 **음성 파이프라인**입니다. 청취(VAD·STT)는 Jetson 로컬,
정보 구조화(LLM)는 **GMS API 호출**, 안내 음성은 **사전녹음 재생**으로 동작합니다.

```
트리거(VISION) → 네트워크 확인 → VAD 게이트 → STT → 환각 가드 → LLM 정보추출(GMS)
                              ↘ 오프라인: 안전 안내 + 관제 전송 대기
                              ↘ STT 완료 후 GMS 실패: 33-8 폴백
              → 규칙 triage → 관제 보고(시뮬) → 안내 음성(사전녹음)
```

## 확정된 스택 (측정·팀 결정 근거)

| 단계 | 선택 | 근거 |
|------|------|------|
| VAD | Silero VAD (로컬) | 노이즈 1차 컷, torch 기반 경량 |
| STT | faster-whisper **`small`** (로컬, 젯슨은 CPU/int8) | 저SNR·약한발화 강건성 |
| LLM | **`gpt-5.4-mini`** (GMS API) | Jira 118 프롬프트 v2 실측 44/44 완전 정답으로 선정. 로컬 3b는 젯슨 피크 5.62GB·OOM([근거](docs/메모리-예산.md)) |
| LLM 폴백 | 키워드 파서(`llm.keyword_extract`) | STT 완료 후 GMS 호출만 실패한 경우의 축소 보고 |
| 안내 음성 | **승인된 사전녹음 WAV 재생**(`assets/`) | TTS 모델 미탑재로 RAM 절약. 형식 검사는 `python -m tools.validate_guide_assets` |
| 등급 | 규칙(`safety.triage_rule`) | LLM 자유판단 배제, 재현·설명 가능 |

## 폴더 구성

| 경로 | 역할 |
|------|------|
| `sentinel_voice/` | 실제 음성 서비스 패키지. 설정·오디오·안전 규칙·GMS·파이프라인 |
| `sentinel_voice/config.py` | device/compute 자동 감지, GMS·모델·튜닝 파라미터, `.env` 로드 |
| `sentinel_voice/audio.py` | 오디오 로더(16kHz mono float32 통일) |
| `sentinel_voice/safety.py` | STT 환각 가드, LLM 출력 보정, 규칙 기반 triage |
| `sentinel_voice/llm.py` | GMS 호출 + 33-8 키워드 폴백 (`extract()` 단일 진입점) |
| `sentinel_voice/gms_resilience.py` | GMS 장애 분류·제한 재시도·호스트 도달성 검사 |
| `sentinel_voice/session_gate.py` | 신규 STT 세션 시작 전 GMS 가용성 게이트 |
| `sentinel_voice/report_delivery.py` | 관제 전송 대기/대기열 인계 상태 계약 |
| `sentinel_voice/report_lifecycle.py` | 관제 ACK·Mission Manager 재개 승인 상태머신 |
| `sentinel_voice/pipeline.py` | 엔드투엔드 실행(마이크/파일) |
| `sentinel_voice/conversation.py` | 5단계 다턴 상태머신과 VAD·STT·구조화 결과 4분류 |
| `sentinel_voice/guide_audio.py` | 승인 문구 목록, WAV 형식 검사, 안전 재생 결과 |
| `tools/` | 배포 전 환경·오디오·사전녹음 자산 점검 |
| `bench/` | 측정용 다회차 벤치(지연·일관성) |
| `tests/` | 하드웨어 없이 실행 가능한 상태머신·안전 규칙 단위 테스트 |
| `prompts/` | 정보 추출 프롬프트(진단 금지, 사실만) |
| `docs/` | 실행 런북, 안전 정책, 메모리·정량·오디오 검증 기준과 [팀 공통 용어집](docs/음성-파이프라인-용어집.md) |

AI·음성 담당이 아닌 팀원은 세부 문서를 읽기 전에
[`docs/음성-파이프라인-용어집.md`](docs/음성-파이프라인-용어집.md)에서
약어, 평가 지표, 보고 필드와 장애 처리 용어를 확인할 수 있습니다.
오디오 검사 코드가 수집하는 항목과 산출물은
[`docs/오디오-입출력-검증-도구.md`](docs/오디오-입출력-검증-도구.md)에 설명합니다.
실제 장비의 합격 여부는 별도 검증 절차로 판정합니다.
사전녹음 파일 목록과 제작·청취·에코 검증은
[`docs/사전녹음-안내-음성.md`](docs/사전녹음-안내-음성.md)를 따릅니다.

가중치·녹음 데이터·`results/`·**`.env`(GMS 키)**는 커밋하지 않습니다(`.gitignore`).

상태머신 단위 테스트:

```bash
cd ai/stt
python -m unittest discover -s tests -v
```

## GMS 설정 (필수)

```bash
# ai/stt/.env 생성 후 GMS_KEY를 실제 팀 키로 교체
cp .env.example .env       # Linux/Jetson
# copy .env.example .env   # Windows Miniforge Prompt
```

> GMS Key는 팀 크레딧과 연결된 비밀 값입니다. 코드·문서·커밋에 절대 넣지 마세요.
> GMS 키는 장기적으로 관제 백엔드의 환경 변수 또는 비밀 저장소에서 관리합니다.
> Jetson 직접 호출을 사용하는 개발 단계에서는 `ai/stt/.env`에만 두며 커밋하지 않습니다.
> 네트워크 단절이 확인되면 신규 STT 대화를 시작하지 않습니다. 이미 STT가 완료된 뒤
> GMS 호출만 실패한 경우에 한해 `llm.py`의 33-8 키워드 폴백을 사용합니다.

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
[`docs/음성-세션-보고-스키마.md`](docs/음성-세션-보고-스키마.md)를 따릅니다.
관제 보고 생성부터 ACK 확인, Mission Manager 재개 승인, Closing 재생과 후속
MQTT·ROS 2 연결 경계는
[`docs/보고-ACK-탐사-재개.md`](docs/보고-ACK-탐사-재개.md)에 용어와 사례를 포함해 설명합니다.

GMS와 키워드 폴백은 인원 수·이동 가능 여부·긴급 상태 언급만 추출합니다.
응답 감지 여부와 종료 사유는 VAD·상태머신이 결정합니다.

색상 등급은 최종 구조 우선순위가 아니라 관제 검토용 참고값입니다. 로봇은 이를 요구조자에게
직접 안내하지 않으며, 구조 ETA는 관제가 제공한 유효한 값만 승인된 템플릿으로 전달합니다.
세부 계약은 [`docs/대화-안전-정책.md`](docs/대화-안전-정책.md)를 따릅니다.

---

## Jetson Orin Nano 배포

x86(개발 PC) → ARM64(Jetson) 아키텍처 차이와 8GB 메모리 제약이 최대 함정입니다.
`torch`·`faster-whisper(CTranslate2)`를 **일반 pip로 설치하면 거의 반드시 막힙니다.**

> ✅ **2026-07-24 실측으로 검증된 절차는 [`docs/젯슨-실행-런북.md`](docs/젯슨-실행-런북.md)를 따르세요.**
> 아래 STEP들은 초기 계획으로, 실전에서 일부가 달랐습니다 — 특히 **jetson-containers 경로는
> 디스크 초과(이미지 19.6GB)로 실패**했고 **네이티브 설치가 정답**이었습니다. 시계 리셋(RTC 없음),
> numpy<2, STT CPU 실행, page cache OOM 등 함정도 런북의 트러블슈팅에 정리돼 있습니다.

### STEP 0 — 기본 준비

```bash
cat /etc/nv_tegra_release        # JetPack 6.2 기대
sudo pip3 install -U jetson-stats && sudo reboot   # jtop (RAM/GPU/온도 감시)
sudo nvpmodel -m 0 && sudo jetson_clocks           # 최대 성능 모드

# 8GB 스왑 (모델 로딩 중 OOM 방지)
sudo fallocate -l 8G /swapfile && sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

### STEP 1 — 런타임 구축 (권장: jetson-containers)

의존성 지옥을 피하려면 NVIDIA 공식 [jetson-containers](https://github.com/dusty-nv/jetson-containers)의
사전 빌드 이미지를 씁니다.

```bash
git clone https://github.com/dusty-nv/jetson-containers
bash jetson-containers/install.sh
jetson-containers run $(autotag faster-whisper)   # STT 환경
```

LLM은 GMS API 호출이므로 젯슨에 모델을 올리지 않습니다 — `pip install openai` + `.env`의
`GMS_KEY`만 있으면 됩니다(위 "GMS 설정" 참고).

> 네이티브 설치를 택할 경우 `torch`는 **반드시 NVIDIA Jetson 전용 휠**만 사용하고,
> `faster-whisper`가 GPU로 안 잡히면 **whisper.cpp(CUDA 빌드)**로 대체합니다(파라미터 이식 가능).

### STEP 1.5 — 로컬 모델 가중치 사전 캐싱 ⚠️

`faster-whisper`·`silero-vad`는 **첫 로드 시 인터넷에서 가중치를 내려받는다**
(각각 HuggingFace / torch.hub). 온라인 음성 세션 중 다운로드 지연을 방지하기 위해,
**반드시 온라인 상태에서 한 번 로드해 캐시를 채운 뒤** 필드에 투입한다.

```bash
cd ai/stt
python -m tools.check_env --load  # STT/VAD 로드 → 캐시 생성 + GMS 실호출 점검
```

> 안내 음성은 사전녹음 WAV(`assets/`, 저장소에 포함)라 캐싱이 필요 없고, LLM(GMS)은 온라인 전용
> — 네트워크 단절이 확인되면 신규 STT 대화는 시작하지 않는다. 33-8 폴백은
> STT 완료 후 GMS 호출만 실패한 경우에 사용한다.

> 캐시 위치(참고): `~/.cache/huggingface`, `~/.cache/torch/hub`. 오프라인 배포 이미지를
> 만들 때 이 디렉터리를 함께 포함하면 재현이 쉽다.

### STEP 2 — 코드 이식 & 점검

```bash
# 이 저장소를 젯슨에 clone 후
cd ai/stt
python -m tools.check_env          # 임포트/CUDA/장치/GMS/스왑 점검
python -m tools.check_env --load   # STT/VAD 실제 로드까지 최종 확인
```

`python -m tools.check_env`가 전부 `[OK]`면 STT 구동 준비 완료입니다.
`[FAIL]`부터 해결하세요.

BRIO 100 마이크와 Bluetooth 스피커의 실제 장치 선택·녹음·재생은
[`docs/오디오-입출력-검증.md`](docs/오디오-입출력-검증.md)의 절차로 별도 검증합니다.

### STEP 3 — 실행

```bash
python -m sentinel_voice.pipeline  # 1=마이크 8초, 2=파일 / 트리거는 VISION 기본
```

### STEP 4 — 메모리 절약 3대 전략 (8GB 필수)

1. **STT int8** — `config.py`가 젯슨에서 자동으로 `int8` 선택(float16 대비 메모리 절반).
   강제하려면 `SENTINEL_COMPUTE=int8`.
2. **안내 음성 사전녹음** — ✅ 재생·검증 코드 적용됨. 실제 승인 WAV는 녹음·청취 검수 후 포함(MeloTTS 미탑재).
3. **LLM 미탑재** — ✅ 적용됨. GMS API 호출로 젯슨 LLM RAM 0 (구 로컬 3b는 피크 5.62GB였음).

### STEP 5 — 측정 (보고서 핵심 수치)

`jtop`을 켠 채로 벤치를 돌려 기록합니다.

```bash
python -m bench.pipeline_bench   # results/pipeline_bench_summary.csv
```

| 항목 | 방법 | 목표 |
|------|------|------|
| RAM 점유 | jtop MEM | 8GB 안에서 여유 |
| STT/LLM/E2E 지연 | 벤치 로그 | 실용 범위인가 |
| GPU/CPU/온도/전력 | jtop / tegrastats | 스로틀링 없나 |
| ⭐ **비전(YOLO)+SLAM 동시 구동** | 팀원과 통합 | **진짜 시험** — OOM·FPS 저하 |

> 가장 중요한 측정은 마지막 줄입니다. GMS 전환으로 오디오 예상 피크가 ~3.3GB로 줄었지만
> (LLM 미탑재), 통합 실측으로 확인하기 전까지는 여유를 단정하지 않습니다. 부족하면 STT `small`→`base` 검토.

> 📄 RAM 예산과 실측 기록은 [`docs/메모리-예산.md`](docs/메모리-예산.md)에 정리합니다(팀 공유용).
> 예상 예산표 + 젯슨에서 채우는 실측 템플릿이 들어 있습니다.
> 전체 STT·GMS·E2E·자원 통과 기준과 공통 결과 필드는
> [`docs/정량-검증-기준.md`](docs/정량-검증-기준.md)를 따릅니다.

### GMS 후보 모델 비교

STT를 다시 실행하지 않고 합성 발화 20건의 정답 라벨을 기준으로 GMS 모델의
정보 추출 품질·지연·일관성을 비교합니다.

```bash
# 데이터셋과 모델 목록만 검사(API 호출·크레딧 사용 없음)
python -m bench.gms_model_bench --dry-run

# 실제 호출은 예정 호출 수 확인 후 --confirm-live를 명시해야 함
python -m bench.gms_model_bench \
  --models gpt-5-nano \
  --case-id all-fields-urgent \
  --runs 1 \
  --confirm-live

# 일부 모델만 시험
python -m bench.gms_model_bench \
  --models gpt-5-nano,gpt-5.4-nano \
  --runs 1 \
  --confirm-live
```

기본 후보는 `gpt-5-nano`, `gemini-3.5-flash`, `claude-haiku-4-5-20251001`,
`claude-sonnet-4-6`, `claude-opus-4-8`, `gpt-5.4-mini`, `gpt-5.4-nano`입니다.
최종 운영 모델은 `gpt-5.4-mini`이며, 모델명은 GMS에서 실제 허용하는 ID와
다를 수 있으며, 사용할 수 없는 모델은 전체 시험을 중단하지 않고 오류 건수로
기록됩니다. Claude와 Gemini는 OpenAI 호환 주소가 아닌 GMS의 Anthropic·Gemini
전용 엔드포인트로 호출합니다.

결과는 `results/gms-bench/`에 생성되며 기본적으로 Git에 포함되지 않습니다.
상세한 판정 기준과 결과 파일 설명은
[`docs/GMS-모델-비교-런북.md`](docs/GMS-모델-비교-런북.md)를 참고합니다.
단계별 실측 결과와 모델 선정 근거는
[`docs/GMS-모델-비교-결과.md`](docs/GMS-모델-비교-결과.md)에 누적합니다.

---

## 환경 변수 (config 오버라이드)

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `SENTINEL_DEVICE` | 자동(cuda/cpu) | 강제 지정 (젯슨 STT는 `cpu` — CTranslate2 CUDA 없음) |
| `SENTINEL_COMPUTE` | Jetson=int8, PC=float16 | STT 양자화 |
| `SENTINEL_STT_MODEL` | small | tiny/base/small/medium/large-v3 |
| `SENTINEL_LLM` | gpt-5.4-mini | GMS 모델명 |
| `GMS_KEY` | (없음, **필수**) | GMS API 키 — `ai/stt/.env`로 관리, 커밋 금지 |
| `SENTINEL_GMS_BASE` | gms.ssafy.io/…/v1 | GMS OpenAI 호환 엔드포인트 |
| `SENTINEL_LLM_TIMEOUT` | 10 | STT 완료 후 GMS 호출 시간 초과 시 33-8 키워드 폴백 |
| `SENTINEL_GMS_MAX_ATTEMPTS` | 2 | 최초 호출을 포함한 최대 GMS 호출 횟수 |
| `SENTINEL_GMS_RETRY_DELAY` | 0.5 | 일시 장애 재시도 전 대기 시간(초) |
| `SENTINEL_GMS_PROBE_TIMEOUT` | 2 | 신규 세션 전 GMS 호스트 연결 확인 제한 시간(초) |

GMS 장애 분류와 관제 전송 대기 상태는
[`docs/GMS-장애-대응.md`](docs/GMS-장애-대응.md)를 따릅니다.
관제 ACK와 탐사 재개 Closing 규칙은
[`docs/보고-ACK-탐사-재개.md`](docs/보고-ACK-탐사-재개.md)를 따릅니다.

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

## 알려진 이슈

- **사투리 미지원:** 현재 검증·합격 범위는 표준 한국어와 일반 구어체다. 사투리
  인식 성능은 측정하지 않으며 최종 STT 점수에도 포함하지 않는다.
- **근-침묵 환각**: 완전 무음이 아닌 매우 작은 잔향에서 STT가 헛단어를 뱉는 경우가 관측됨.
  현재 `SILENCE_RMS`(원본 RMS 게이트) + `is_valid_stt`(반복/무음확률/프롬프트복사 컷)로 방어하나
  완전하지 않음. 개선 방향: 블록리스트 + 침묵 재녹음 재측정 + STT 프롬프트에서 특정 단어 제거.
- `bench/`는 `data/` 샘플이 있어야 의미 있음(없으면 해당 시나리오 `NO_FILE` 스킵). 녹음은 개발 PC에서 준비.
