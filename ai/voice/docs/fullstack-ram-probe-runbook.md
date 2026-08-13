# 통합 스택 + 로컬 LLM 메모리 실측 런북

> 목적: [docs/08-AI-음성.md](../../../docs/08-AI-음성.md) 33.13.5의 "전부 온디바이스
> 구성 추정 피크 약 9.2GB"를 실측으로 확정한다. 전체 스택이 뜬 상태에서
> (1) CUDA 연속 버퍼 할당 한계와 (2) 가능하면 실제 3B LLM 로드를 측정한다.
> 예상 소요 약 20분.

## 전제 조건 — 하나라도 아니면 진행하지 않는다

- [ ] 데모·운용 중이 아니다. 실험 중 스택 프로세스가 OOM으로 죽을 수 있다.
- [ ] **바퀴가 바닥에서 떨어져 있거나 모터 보드 전원이 분리돼 있다 — 선택이
      아니라 필수다.** 이 차량에는 물리 E-Stop이 없고, `demo_up.sh`는 센서
      보드를 감지하면 아래 B 명령에 없어도 `enable_esp32:=true`를 자동
      주입한다(S15P11A301-256). 즉 B 구성(Nav2·안전·탐사)에서는 모터 명령
      경로가 살아나 로봇이 주행을 시도할 수 있다. `enable_safety`를 빼는
      것만으로는 충분한 안전 조치가 아니다.
- [ ] 위 전제를 만족할 수 없으면 B 명령에 `enable_esp32:=false`를 **명시**해
      시리얼 경로를 차단한 채 측정하고, 모터 tty 사용 프로세스가 없는지
      `fuser -v /dev/ttyUSB2`로 확인한다. 이 경우 esp32 브리지 2개만큼 RAM이
      빠진 측정임을 결과에 함께 기록한다(2026-08-11 측정이 이 조건이었다).
- [ ] 관제 웹 시청자 0명. (8월 계측 때 WebRTC 시청자 14명이 수치를 오염시켰다)

## 준비 (1회)

젯슨에서 실험 브랜치를 체크아웃한다.

```bash
cd /home/orin/projects/S15P11A301
git fetch origin
git checkout feature/ai/S15P11A301-249-fullstack-ram-probe
```

프로브 자체 검증(하드웨어 무관, 1초):

```bash
cd /home/orin/projects/S15P11A301/ai/voice
/home/orin/projects/S15P11A301/.venv/bin/python -m evaluation.cuda_contig_probe --self-test
mkdir -p /home/orin/experiments/ram-bench
```

`self-test OK`가 나와야 한다.

측정 환경을 기록한다(33.7 규칙: commit·장치·전력 모드 병기). 과거 계측은
15W 모드였으므로 다르면 결과 비교 시 명시해야 한다.

```bash
cd /home/orin/projects/S15P11A301
{
  echo "commit: $(git rev-parse --short HEAD)"
  echo "branch: $(git branch --show-current)"
  cat /etc/nv_tegra_release
  cat /var/lib/nvpmodel/status
  grep "POWER_MODEL" /etc/nvpmodel.conf
  uptime
} > /home/orin/experiments/ram-bench/env.txt 2>&1
cat /home/orin/experiments/ram-bench/env.txt
```

## 실험 A — 스택 없이 기준선 (약 3분)

스택이 완전히 내려간 상태에서 실행한다 (`./scripts/demo_down.sh` 또는
`sudo systemctl stop sentinel-demo`).

```bash
cd /home/orin/projects/S15P11A301/ai/voice

/home/orin/projects/S15P11A301/.venv/bin/python -m evaluation.jetson_resource_bench \
  --label idle-probe --output-dir /home/orin/experiments/ram-bench/idle \
  --baseline-seconds 15 --after-seconds 15 -- \
  /home/orin/projects/S15P11A301/.venv/bin/python -m evaluation.cuda_contig_probe \
    --label idle --output /home/orin/experiments/ram-bench/idle-probe.json

free -m > /home/orin/experiments/ram-bench/idle-free.txt
```

## 실험 B — 전체 스택 기동 후 프로브 (약 10분)

8월 계측(S15P11A301-249)의 B 구성을 재현한다. 터미널 1:

```bash
cd /home/orin/projects/S15P11A301
./scripts/demo_up.sh enable_nav2:=true enable_safety:=true enable_exploration:=true
# 모터 통전 + 바퀴가 바닥에 있으면: enable_safety:=true 를 뺀 채 실행
```

터미널 2 — settle 75초를 기다린 뒤 실행한다. `--baseline-seconds 90`이
"스택만 있는 90초 샘플"(8월 계측과 동일 조건) 역할을 하고, 그 직후
프로브가 돈다.

```bash
sleep 75
cd /home/orin/projects/S15P11A301/ai/voice

/home/orin/projects/S15P11A301/.venv/bin/python -m evaluation.jetson_resource_bench \
  --label fullstack-probe --output-dir /home/orin/experiments/ram-bench/fullstack \
  --baseline-seconds 90 --after-seconds 30 -- \
  /home/orin/projects/S15P11A301/.venv/bin/python -m evaluation.cuda_contig_probe \
    --label fullstack --output /home/orin/experiments/ram-bench/fullstack-probe.json

free -m > /home/orin/experiments/ram-bench/fullstack-free.txt
```

## 실험 C — (ollama가 남아 있을 때만) 실제 3B LLM 로드 (약 5분)

스택이 떠 있는 상태 그대로:

```bash
which ollama && ollama list | grep -i qwen2.5
```

둘 다 확인되면:

```bash
cd /home/orin/projects/S15P11A301/ai/voice

/home/orin/projects/S15P11A301/.venv/bin/python -m evaluation.jetson_resource_bench \
  --label fullstack-ollama --output-dir /home/orin/experiments/ram-bench/ollama \
  --baseline-seconds 10 --after-seconds 30 -- \
  ollama run qwen2.5:3b "안녕하세요"

sudo dmesg | tail -80 > /home/orin/experiments/ram-bench/dmesg-tail.txt \
  || journalctl -k -n 80 --no-pager > /home/orin/experiments/ram-bench/dmesg-tail.txt
```

ollama나 모델이 없으면 이 실험은 생략한다(설치하지 않는다). 실험 B의
프로브가 대체 근거다.

## 종료와 회수

```bash
cd /home/orin/projects/S15P11A301
./scripts/demo_down.sh
git checkout develop

tar czf /home/orin/ram-bench-$(date +%m%d).tar.gz -C /home/orin/experiments ram-bench
```

`ram-bench-*.tar.gz`를 요청자에게 보낸다. 스택이 도중에 죽었으면 그 사실과
journalctl 오류도 함께 알려 준다 — 그것도 결과다. 측정 원본은 개인 평가
자료와 같은 취급으로 Git에 커밋하지 않는다.

## 판정 기준 (참고)

| 관측 | 해석 |
|---|---|
| 실험 B `max_ok_mb` < 1792 | 과거 "연속 1.79GB 할당 실패" 재현 → 로컬 LLM 기각 실측 확정 |
| 실험 B에서 2400이 `skipped_safety` | available 자체가 부족해 시도 불가 → 동일 결론 |
| 실험 C ollama 로드 실패 | 그 자체로 확정 |
| 실험 C 성공 + RAM 피크 7GB 초과 | 합산 추정 검증(잔여 여유를 함께 기록) |
