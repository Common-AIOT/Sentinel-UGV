# Jetson RAM·E2E 자원 측정 런북

> Jira: S15P11A301-119  
> 목적: 로컬 LLM에서 GMS로 전환한 뒤 Jetson RAM 여유와 통합 실행 안정성을 수치로 증명  
> 실행 장비: Jetson Orin Nano 8GB  
> 운영 모델: faster-whisper `small` + Silero VAD + GMS `gpt-5.4-mini` + 사전녹음 WAV

Jetson 터미널에서 그대로 실행하는 작업 순서는
[`Jetson-자원-측정-테스트-매뉴얼.md`](Jetson-자원-측정-테스트-매뉴얼.md)를 따른다.

## 1. 이번 측정이 답해야 하는 질문

1. 로컬 `qwen2.5:3b` 사용 시 피크 `5,620MB`보다 GMS 구성의 피크 RAM이 얼마나
   감소했는가?
2. 음성 파이프라인을 추가한 뒤에도 available RAM이 1GB 이상 남는가?
3. 음성 세션 종료 60초 뒤 RAM·Swap이 baseline으로 돌아오는가?
4. 비전·SLAM과 함께 실행해도 OOM, 열 스로틀링, 비전 FPS 10% 초과 하락이 없는가?

GMS 서버의 메모리는 Jetson RAM에 포함되지 않는다. 이 측정은 **Jetson에서 실제로
사용한 메모리**만 비교한다.

## 2. 측정 도구와 산출물

[`bench/jetson_resource_bench.py`](../bench/jetson_resource_bench.py)는 다음 세 구간을
자동으로 구분한다.

```text
baseline 10초 → 측정 명령 실행 → 종료 후 after 60초
```

각 실행 폴더에는 다음 증적을 생성한다.

| 파일 | 내용 |
|---|---|
| `tegrastats.log` | 시간·구간 표시가 붙은 tegrastats 원문 |
| `resource-samples.csv` | RAM, available RAM, Swap, CPU, GPU, 온도 시계열 |
| `resource-summary.json` | baseline·peak·after, 최소 여유 RAM, 판정 |
| `console.log` | 측정 대상 프로그램의 표준 출력·오류 |

`resource-summary.json`의 주요 필드:

| 필드 | 의미 |
|---|---|
| `ramBaselineMb` | 실행 전 10초 RAM 중앙값 |
| `ramPeakMb` | 측정 명령 실행 중 최대 RAM |
| `ramIncreaseFromBaselineMb` | 음성·통합 실행으로 증가한 RAM |
| `ramAfterMb` | 종료 후 마지막 5개 샘플의 RAM 중앙값 |
| `ramRetainedAfterMb` | 종료 후 baseline보다 남아 있는 RAM |
| `memAvailableMinimumMb` | 실행 중 운영체제가 즉시 사용할 수 있었던 최소 RAM |
| `swapPeakMb` | 실행 중 최대 Swap 사용량 |
| `temperatureMaxC` | 실행 중 측정된 최고 온도 |

## 3. 측정 전 준비

모든 명령은 Jetson의 저장소 루트에서 시작한다.

```bash
cd ~/projects/S15P11A301
git switch develop
git pull --ff-only origin develop
source .venv/bin/activate
cd ai/stt
```

환경과 모델 로드를 확인한다.

```bash
python check_env.py --load
```

다음 조건을 기록한다.

```bash
mkdir -p results/jetson-resource
{
  echo "=== date ==="
  date --iso-8601=seconds
  echo "=== git ==="
  git rev-parse HEAD
  echo "=== L4T ==="
  cat /etc/nv_tegra_release
  echo "=== power mode ==="
  sudo nvpmodel -q
  echo "=== clocks ==="
  sudo jetson_clocks --show
  echo "=== memory ==="
  free -h
  echo "=== config ==="
  python -c "from sentinel_voice import config; print(config.summary())"
} | tee results/jetson-resource/environment.txt
```

비교 실행은 아래 조건을 반드시 동일하게 유지한다.

- Jetson 전원 모드
- GUI 실행 여부
- `jetson_clocks` 적용 여부
- 연결한 카메라·마이크·스피커
- 테스트 음원과 반복 횟수
- 백그라운드 서비스

## 4. 1단계 — GMS 음성 단독 측정

`data/`에 벤치 WAV가 실제로 존재하는지 먼저 확인한다.

```bash
find data -type f -name '*.wav' -print
```

파일이 없으면 `pipeline_bench.py`가 `NO_FILE`로 스킵하므로 STT·GMS E2E 측정으로
인정할 수 없다. Jira 120용 승인 WAV를 복사한 뒤 실행한다.

```bash
mkdir -p results/jetson-resource/voice-gms
python -m bench.jetson_resource_bench \
  --label voice-gms \
  --output-dir results/jetson-resource/voice-gms \
  --baseline-seconds 10 \
  --after-seconds 60 \
  -- bash -lc 'python -u -m bench.pipeline_bench 2>&1 | tee results/jetson-resource/voice-gms/console.log'
```

완료 후 확인:

```bash
cat results/jetson-resource/voice-gms/resource-summary.json
```

필수 통과 조건:

- `commandExitCode = 0`
- `memAvailableMinimumMb >= 1024`
- OOM 또는 프로세스 강제 종료 0건
- 종료 후 Swap 증가량 256MB 이하

## 5. 2단계 — 음성 30회 안정성

실제 세션 실행기가 30회 반복을 지원하는 시점에 같은 래퍼로 감싼다. 단순 GMS 요청
30회가 아니라 **VAD → STT → GMS → 보고 → 안내 음성**의 전체 세션이어야 한다.

```bash
python -m bench.jetson_resource_bench \
  --label voice-30sessions \
  --output-dir results/jetson-resource/voice-30sessions \
  --baseline-seconds 10 \
  --after-seconds 60 \
  -- <30회 음성 세션 실행 명령>
```

`<...>`는 셸에 그대로 입력하는 문구가 아니다. 실제 세션 CLI가 확정되면 해당 명령으로
교체한다. 현재 `pipeline_bench.py`는 오디오 재생·관제 ACK까지 포함하지 않으므로 이
단계의 완전한 대체물이 아니다.

## 6. 3단계 — 비전·SLAM 통합 측정

안전 때문에 로봇 바퀴를 띄우거나 주행을 비활성화한 정지 상태에서 진행한다.

측정 순서:

1. 비전만 실행해 baseline FPS·RAM 측정
2. 비전+SLAM 측정
3. 비전+SLAM을 실행한 상태에서 음성 세션 측정

각 조합을 동일한 래퍼로 실행하고, 비전 로그에서 FPS를 별도 추출한다. 통합 launch
명령은 Jetson/ROS 2 담당자가 확정한 실제 launch 파일을 사용해야 하며 임의로 추정하지
않는다.

통과 조건:

- OOM·프로세스 강제 종료 0건
- 음성 처리 중 available RAM 1GB 이상
- 열 스로틀링 0건
- 음성 추가 후 비전 FPS 하락률 10% 이하
- 30회 후 RAM 증가율 10% 이하

FPS 하락률:

```text
(비전 단독 FPS - 음성 동시 FPS) / 비전 단독 FPS × 100
```

## 7. 로컬 LLM 대비 결과표

실측 완료 후 [`메모리-예산.md`](메모리-예산.md)의 표를 채운다.

| 구성 | 피크 RAM | 최소 available RAM | 종료 후 RAM | 피크 Swap | 판정 |
|---|---:|---:|---:|---:|---|
| 로컬 qwen2.5:3b | 5,620MB | 미측정 | 미측정 | 미측정 | OOM 위험 확인 |
| GMS 음성 단독 | 측정 예정 | 측정 예정 | 측정 예정 | 측정 예정 | 미측정 |
| 비전+SLAM+GMS 음성 | 측정 예정 | 측정 예정 | 측정 예정 | 측정 예정 | 미측정 |

RAM 절감량과 절감률:

```text
절감량(MB) = 5,620 - GMS 피크 RAM
절감률(%) = 절감량 / 5,620 × 100
```

기존 로컬 LLM 측정과 GUI·전원 모드 등의 조건이 다르면 직접적인 A/B 실험이라고
표현하지 않고 **과거 기준 대비 참고 비교**라고 명시한다.

## 8. 팀에 전달할 결과

- 장비·전원·Git commit·환경 정보
- 각 실행의 원본 tegrastats 로그
- baseline·peak·after RAM과 최소 available RAM
- Swap·최고 온도·CPU/GPU 사용량
- GMS 전환 전후 RAM 차이와 조건 차이
- 통합 시 비전 FPS 변화
- 실패 로그와 재현 명령

API 키, 인증 헤더, 개인 식별 가능 음성·전사문은 커밋하지 않는다.
