# STT 음성 파이프라인

요구조자의 음성을 듣고 → 부상 정보를 구조화 → 관제 서버에 보고 → "전달했으니 안심하라"는
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
| LLM | **`gpt-5-nano`** (GMS API) | 팀 결정 2026-07-24. 로컬 3b는 젯슨 실측 피크 5.62GB·OOM([근거](docs/메모리-예산.md)) |
| LLM 폴백 | 키워드 파서(`llm.keyword_extract`) | STT 완료 후 GMS 호출만 실패한 경우의 축소 보고 |
| TTS | **사전녹음 wav 재생**(`assets/`) | RAM 절약 1순위(−1.3~2GB). 생성은 PC에서 `make_tts_assets.py` |
| 등급 | 규칙(`safety.triage_rule`) | LLM 자유판단 배제, 재현·설명 가능 |

## 파일 구성

| 파일 | 역할 |
|------|------|
| `config.py` | device/compute **자동 감지**, GMS·모델·튜닝 파라미터, `.env` 로드 |
| `utils.py` | 오디오 로더(16kHz mono float32 통일) |
| `safety.py` | STT 환각 가드 + LLM 출력 보정 + 규칙 기반 triage |
| `llm.py` | **GMS 호출 + 33-8 키워드 폴백** (`extract()` 단일 진입점) |
| `pipeline.py` | 엔드투엔드 실행 (마이크/파일) |
| `make_tts_assets.py` | 고정 안내 문구 wav 생성 (개발 PC, MeloTTS 필요) |
| `check_env.py` | **젯슨 구동 가능 여부 점검** (`--load`로 GMS 실호출까지) |
| `bench/pipeline_bench.py` | 측정용 다회차 벤치(지연·일관성) |
| `prompts/triage_extract.txt` | 정보 추출 프롬프트(진단 금지, 사실만) |
| `docs/대화-안전-정책.md` | 위험도 참고값, 무응답/시스템 실패 구분, ETA·안내 문구 정책 |
| `docs/정량-검증-기준.md` | STT·GMS·E2E·Jetson 자원 검증 지표, 통과 기준, 기록 형식 |

가중치·녹음 데이터·`results/`·**`.env`(GMS 키)**는 커밋하지 않습니다(`.gitignore`).

## GMS 설정 (필수)

```bash
# ai/stt/.env 파일 생성 (커밋 금지 — .gitignore 등록됨)
echo "GMS_KEY=여기에_팀_GMS_키" > .env
```

> GMS Key는 팀 크레딧과 연결된 비밀 값입니다. 코드·문서·커밋에 절대 넣지 마세요.
> GMS 키는 장기적으로 관제 백엔드의 환경 변수 또는 비밀 저장소에서 관리합니다.
> Jetson 직접 호출을 사용하는 개발 단계에서는 `ai/stt/.env`에만 두며 커밋하지 않습니다.
> 네트워크 단절이 확인되면 신규 STT 대화를 시작하지 않습니다. 이미 STT가 완료된 뒤
> GMS 호출만 실패한 경우에 한해 `llm.py`의 33-8 키워드 폴백을 사용합니다.

## 추출 스키마

```json
{
  "consciousness": "명료|혼미|통증반응|무반응|미확인",
  "speech":        "완전문장|단어만|신음만|불가|미확인",
  "pain_location": ["부위", ...],
  "hazard":        ["가스|불|연기|붕괴", ...],
  "can_move":      "가능|불가|미확인",
  "additional_victims": 0,
  "raw_note": "한 줄 요약"
}
```
→ `triage_rule`이 색상 등급(적색/황색/녹색)을 **규칙으로** 산출.

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
python check_env.py --load     # STT/VAD 로드 → 캐시 생성 + GMS 실호출 점검(온라인 필요)
```

> TTS는 사전녹음 wav(`assets/`, 저장소에 포함)라 캐싱이 필요 없고, LLM(GMS)은 온라인 전용
> — 네트워크 단절이 확인되면 신규 STT 대화는 시작하지 않는다. 33-8 폴백은
> STT 완료 후 GMS 호출만 실패한 경우에 사용한다.

> 캐시 위치(참고): `~/.cache/huggingface`, `~/.cache/torch/hub`. 오프라인 배포 이미지를
> 만들 때 이 디렉터리를 함께 포함하면 재현이 쉽다.

### STEP 2 — 코드 이식 & 점검

```bash
# 이 저장소를 젯슨에 clone 후
cd ai/stt
python check_env.py            # 임포트/CUDA/장치/LLM 보유/스왑 점검
python check_env.py --load     # STT/VAD 실제 로드까지 최종 확인
```

`check_env.py`가 전부 `[OK]`면 STT 구동 준비 완료입니다. `[FAIL]`부터 해결하세요.

### STEP 3 — 실행

```bash
python pipeline.py             # 1=마이크 8초, 2=파일  /  트리거는 VISION 기본
```

### STEP 4 — 메모리 절약 3대 전략 (8GB 필수)

1. **STT int8** — `config.py`가 젯슨에서 자동으로 `int8` 선택(float16 대비 메모리 절반).
   강제하려면 `SENTINEL_COMPUTE=int8`.
2. **TTS 사전녹음** — ✅ 적용됨. 고정 안내 문구는 `assets/` wav 재생(MeloTTS 미탑재, 1~2GB 절약).
3. **LLM 미탑재** — ✅ 적용됨. GMS API 호출로 젯슨 LLM RAM 0 (구 로컬 3b는 피크 5.62GB였음).

### STEP 5 — 측정 (보고서 핵심 수치)

`jtop`을 켠 채로 벤치를 돌려 기록합니다.

```bash
python bench/pipeline_bench.py   # results/pipeline_bench_summary.csv
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

---

## 환경 변수 (config 오버라이드)

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `SENTINEL_DEVICE` | 자동(cuda/cpu) | 강제 지정 (젯슨 STT는 `cpu` — CTranslate2 CUDA 없음) |
| `SENTINEL_COMPUTE` | Jetson=int8, PC=float16 | STT 양자화 |
| `SENTINEL_STT_MODEL` | small | tiny/base/small/medium/large-v3 |
| `SENTINEL_LLM` | gpt-5-nano | GMS 모델명 |
| `GMS_KEY` | (없음, **필수**) | GMS API 키 — `ai/stt/.env`로 관리, 커밋 금지 |
| `SENTINEL_GMS_BASE` | gms.ssafy.io/…/v1 | GMS OpenAI 호환 엔드포인트 |
| `SENTINEL_LLM_TIMEOUT` | 10 | STT 완료 후 GMS 호출 시간 초과 시 33-8 키워드 폴백 |

## 개발 PC(x86)에서 테스트

```bash
pip install -r requirements.txt   # 젯슨과 달리 x86은 이대로 설치 가능(torch는 별도)
python check_env.py
```

## 알려진 이슈

- **근-침묵 환각**: 완전 무음이 아닌 매우 작은 잔향에서 STT가 헛단어를 뱉는 경우가 관측됨.
  현재 `SILENCE_RMS`(원본 RMS 게이트) + `is_valid_stt`(반복/무음확률/프롬프트복사 컷)로 방어하나
  완전하지 않음. 개선 방향: 블록리스트 + 침묵 재녹음 재측정 + STT 프롬프트에서 특정 단어 제거.
- `bench/`는 `data/` 샘플이 있어야 의미 있음(없으면 해당 시나리오 `NO_FILE` 스킵). 녹음은 개발 PC에서 준비.
