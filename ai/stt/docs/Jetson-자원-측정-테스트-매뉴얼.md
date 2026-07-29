# Jetson 자원 측정 테스트 매뉴얼

> Jira: S15P11A301-119  
> 대상 장비: Jetson Orin Nano 8GB  
> 목적: 자원 측정 프로그램의 실기기 동작 확인 후 GMS 음성 파이프라인 RAM 측정  
> 관련 설계·판정 기준: [`Jetson-자원-측정-런북.md`](Jetson-자원-측정-런북.md)

이 문서는 Jetson 터미널에서 위에서부터 순서대로 실행하는 작업용 매뉴얼이다.
한 단계에서 오류가 발생하면 다음 단계로 넘어가지 말고 명령과 전체 출력을 보존한다.

## 0. 주의사항

- 로봇을 주행시키지 않는다.
- 기존 팀원 작업 폴더를 삭제하거나 Git에 추가하지 않는다.
- `.env`와 GMS API 키를 출력하거나 커밋하지 않는다.
- `results/` 산출물은 우선 Jetson 로컬에 보존하고 검토 전에는 커밋하지 않는다.
- 실제 음성 측정은 테스트 WAV가 Jetson에 준비된 뒤 진행한다.

## 1. 저장소와 Git 상태 확인

어느 폴더에서 시작해도 된다.

```bash
cd ~/projects/S15P11A301
git status --short
```

예전에 설치된 아래 폴더가 untracked로 표시될 수 있다.

```text
jetson/ros2_ws/src/usb_cam/
jetson/ros2_ws/src/ydlidar_ros2_driver/
```

이 폴더는 이번 음성 작업 범위가 아니므로 삭제하거나 `git add`하지 않는다.

## 2. Jira 119 브랜치 받기

```bash
git fetch origin
git switch --track origin/feature/ai/S15P11A301-119-jetson-resource-benchmark
```

이미 브랜치가 있다는 메시지가 나오면 다음을 실행한다.

```bash
git switch feature/ai/S15P11A301-119-jetson-resource-benchmark
git pull --ff-only
```

현재 브랜치 확인:

```bash
git branch --show-current
```

정상 출력:

```text
feature/ai/S15P11A301-119-jetson-resource-benchmark
```

측정 프로그램 커밋 확인:

```bash
git log -1 --oneline
```

커밋 `20242eb` 또는 이 커밋을 포함한 후속 커밋이어야 한다.

## 3. Python 가상환경 활성화

저장소 루트에서 실행한다.

```bash
source .venv/bin/activate
which python
python --version
```

정상 경로 예:

```text
/home/orin/projects/S15P11A301/.venv/bin/python
```

측정 프로그램이 있는 폴더로 이동한다.

```bash
cd ~/projects/S15P11A301/ai/stt
ls -l bench/jetson_resource_bench.py
```

## 4. tegrastats 단독 확인

```bash
which tegrastats
tegrastats --interval 1000
```

정상이라면 다음과 비슷한 내용이 1초마다 출력된다.

```text
RAM 2100/7620MB ... CPU [...] GR3D_FREQ ... cpu@52C
```

2~3줄을 확인한 뒤 `Ctrl+C`로 종료한다. 아무것도 출력되지 않거나
`command not found`가 나오면 다음 단계로 넘어가지 않는다.

## 5. 측정 프로그램 단위 테스트

Jetson에서 새 측정 도구의 테스트만 실행한다.

```bash
cd ~/projects/S15P11A301/ai/stt
python -m unittest tests.test_jetson_resource_bench -v
```

정상 기준:

```text
Ran 6 tests
OK
```

## 6. 안전한 스모크 테스트

이 단계는 STT·GMS를 실행하지 않는다. 5초 동안 대기하는 Python 명령을 측정하여
`tegrastats` 수집과 결과 저장이 정상인지 확인한다.

기존 임시 결과를 제거한다.

```bash
rm -rf /tmp/sentinel-resource-smoke
```

`/tmp/sentinel-resource-smoke`만 지우는 명령이다. 저장소 파일은 삭제하지 않는다.

측정 실행:

```bash
cd ~/projects/S15P11A301/ai/stt
python -m bench.jetson_resource_bench \
  --label tool-smoke \
  --output-dir /tmp/sentinel-resource-smoke \
  --baseline-seconds 3 \
  --after-seconds 3 \
  -- bash -lc 'python -c "import time; time.sleep(5)"'
```

예상 소요 시간은 약 11초다.

```text
baseline 3초 → 테스트 명령 5초 → after 3초
```

정상 실행 시 마지막에 JSON 요약과 아래 문구가 출력된다.

```text
결과 저장: /tmp/sentinel-resource-smoke
```

## 7. 스모크 결과 확인

생성 파일:

```bash
ls -lh /tmp/sentinel-resource-smoke
```

다음 세 파일이 있어야 한다.

```text
tegrastats.log
resource-samples.csv
resource-summary.json
```

요약 확인:

```bash
cat /tmp/sentinel-resource-smoke/resource-summary.json
```

필수 확인값:

| 필드 | 스모크 통과 조건 |
|---|---|
| `commandExitCode` | `0` |
| `phaseSampleCounts.baseline` | 1 이상 |
| `phaseSampleCounts.command` | 1 이상 |
| `phaseSampleCounts.after` | 1 이상 |
| `ramPeakMb` | 숫자 |
| `memAvailableMinimumMb` | 숫자 |
| `passed` | `true` |

구간별 CSV 행 개수 확인:

```bash
python - <<'PY'
import csv
from collections import Counter

path = "/tmp/sentinel-resource-smoke/resource-samples.csv"
with open(path, encoding="utf-8-sig", newline="") as handle:
    counts = Counter(row["phase"] for row in csv.DictReader(handle))
print(dict(counts))
PY
```

정상 예:

```text
{'baseline': 3, 'command': 5, 'after': 3}
```

1초 주기와 프로세스 시작·종료 시점에 따라 각 개수는 1개 정도 달라질 수 있다.

## 8. 작업자 전달 항목

스모크 테스트가 끝나면 다음 명령의 출력을 담당자에게 전달한다.

```bash
cat /tmp/sentinel-resource-smoke/resource-summary.json
```

```bash
git status --short
```

오류가 발생했다면 아래 내용도 함께 전달한다.

- 실패한 명령 전체
- 오류가 시작된 첫 줄부터 마지막 줄까지
- `which tegrastats` 결과
- `python --version` 결과
- `git branch --show-current` 결과

## 9. 실제 GMS 음성 측정 전 점검

스모크 테스트가 통과한 뒤에만 진행한다.

환경 확인:

```bash
cd ~/projects/S15P11A301/ai/stt
python check_env.py --load
```

테스트 WAV 확인:

```bash
find data -type f -name '*.wav' -print
```

`pipeline_bench.py`가 요구하는 WAV가 없다면 실제 STT·GMS 측정은 중단한다.
`NO_FILE` 결과는 모델 로드 일부만 확인할 뿐 E2E 측정으로 인정하지 않는다.

GMS 키는 값 자체를 출력하지 않고 설정 여부만 확인한다.

```bash
python - <<'PY'
import os
print("GMS_KEY configured:", bool(os.getenv("GMS_KEY")))
PY
```

키가 `.env`에만 있고 현재 셸 환경에 로드되지 않았다면 프로젝트에서 정한 방식으로
환경변수를 로드한다. 키 값을 터미널 캡처나 보고서에 포함하지 않는다.

## 10. 실제 GMS 음성 단독 측정

WAV와 환경 검사가 모두 통과한 경우에만 실행한다.

```bash
cd ~/projects/S15P11A301/ai/stt
mkdir -p results/jetson-resource/voice-gms

python -m bench.jetson_resource_bench \
  --label voice-gms \
  --output-dir results/jetson-resource/voice-gms \
  --baseline-seconds 10 \
  --after-seconds 60 \
  -- bash -lc 'python -u -m bench.pipeline_bench 2>&1 | tee results/jetson-resource/voice-gms/console.log'
```

측정 중 프로그램이 멈춘 것처럼 보여도 모델 로드·STT·GMS 처리 중일 수 있다.
다른 명령을 같은 터미널에 입력하지 않는다. 명시적인 오류가 없으면 완료 문구까지 기다린다.

완료 후:

```bash
cat results/jetson-resource/voice-gms/resource-summary.json
```

```bash
cat results/jetson-resource/voice-gms/console.log
```

```bash
git status --short
```

## 11. 실제 측정의 1차 통과 기준

- `commandExitCode = 0`
- `memAvailableMinimumMb >= 1024`
- OOM 또는 프로세스 강제 종료 0건
- `console.log`에 `NO_FILE` 0건
- STT·GMS가 실제로 한 번 이상 실행됨
- 종료 후 Swap 증가량 256MB 이하

이 조건을 통과한 뒤 비전·SLAM 통합 측정으로 넘어간다.
