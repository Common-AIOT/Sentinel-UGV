# ASR shadow 벤치마크와 운영 모델 선정

Jira: `S15P11A301-269`

## 결론

현재 GPU 원격 ASR의 **조건부 기본 모델은 `Qwen/Qwen3-ASR-1.7B`**로 둔다.
L40S에서 동일 합성 음성 9건을 모델당 3회씩 실행했을 때 Qwen3-ASR만 라벨이 있는
발화의 CER·WER가 모두 0이었고, 핵심 표현 보존율 100%, 위험→안전 뒤집힘 0건,
P95 425.847 ms를 기록했다.

폴백 후보는 `faster-whisper turbo`(large-v3-turbo)다. VRAM 2,170 MiB와 P95
367.649 ms로 가장 가벼웠지만 micro CER 0.0291, WER 0.0968로 Qwen보다 정확도가 낮았다.
`large-v3`는 turbo보다 정확하지만 이 작은 코퍼스에서 가장 느렸다.

이 결론은 **후보 선별용이며 운영 최종 승인 결과가 아니다.** 합성 음성은 실제
화자·마이크·잔향·약한 발성을 대표하지 않는다. 최소 3명 × 10발화 × 4조건의 현장
코퍼스를 같은 명령으로 재실행하기 전까지 `CONDITIONAL`로 취급한다.

## L40S 실측

환경: NVIDIA L40S Device3 46,068 MiB, driver 570.211.01, CUDA Driver API 12.8,
PyTorch 2.11.0+cu128, `qwen-asr==0.0.6`, `faster-whisper==1.2.1`, 모델별 동시성 1.

| 모델 | micro CER | micro WER | 핵심 표현 | 위험→안전 | P50 / P95 | RTF | VRAM |
|---|---:|---:|---:|---:|---:|---:|---:|
| Qwen3-ASR-1.7B | **0.0000** | **0.0000** | 100% | 0 | **260.667 / 425.847 ms** | **0.1024** | 5,014 MiB |
| faster-whisper large-v3 | 0.0097 | 0.0645 | 100% | 0 | 417.319 / 509.149 ms | 0.1607 | 3,528 MiB |
| faster-whisper large-v3-turbo | 0.0291 | 0.0968 | 100% | 0 | 302.578 / **367.649 ms** | 0.1134 | **2,170 MiB** |

각 모델은 9개 케이스 × 3회, 총 27요청 모두 성공했다. 한국어 5문장과 영어
2문장에는 이동 가능/불가, 부상 유무, 인원 수를 포함했다. 나머지는 3초 무음과
저진폭 백색 잡음이다. 상세 수치는
`results/asr-shadow-l40s-20260805/asr-shadow-summary.json`과 CSV에 있다.
표의 CER·WER는 발화별 평균이 아니라 전체 편집 수를 전체 정답 단위로 나눈 micro 값이다.

## 중요한 실패 관찰

모델 세 개 모두 무음과 잡음 전용 입력 2개 × 3회에서 전사문을 만들었다.

| 모델 | 무음 예시 | 잡음 예시 |
|---|---|---|
| Qwen3-ASR | `그렇죠.` | `그렇지.` |
| large-v3 | `한글자막 by 한효주` | `시청해주셔서 감사합니다.` |
| large-v3-turbo | `다음 영상에서 만나요.` | `다음 영상에서 만나요.` |

따라서 서버 모델 자체를 무음 판별기로 사용하면 안 된다. 268번 구현처럼 Jetson의
디지털 무음 검사와 Silero VAD를 원격 호출 전에 유지하는 것이 **필수 안전 경계**다.
VAD를 통과하지 못한 오디오는 ASR 서버로 보내지 않고 `NO_RESPONSE`로 처리한다.
VAD를 통과했는데 서버가 실패한 경우만 `VOICE_DETECTED_STT_FAILED`로 처리한다.

## 재현 방법

벤치 도구는 외부 패키지 없이 Python 표준 라이브러리만 사용하며 API 키를 결과나
로그에 기록하지 않는다. HTTP는 loopback SSH 터널만 허용하고 원격 주소는 HTTPS를
요구한다.

```bash
cd ai/stt
export ASR_API_KEY='서버와 동일한 키'

python -m bench.asr_shadow_bench \
  --manifest bench/fixtures/asr-shadow/manifest.jsonl \
  --endpoint qwen=http://127.0.0.1:18100 \
  --runs 3 \
  --output-dir results/asr-shadow-qwen
```

공유 GPU에서는 후보를 동시에 계속 띄워두지 말고 순차 실행한다. 267번 서버 계약의
환경변수만 바꿔 포트 18101에 비교 후보를 임시로 띄울 수 있다.

```bash
# large-v3
export ASR_BACKEND=whisper ASR_MODEL_ID=large-v3 ASR_DTYPE=float16 ASR_PORT=18101
python -m gpu_server

# large-v3-turbo: faster-whisper 모델 별칭은 turbo
export ASR_BACKEND=whisper ASR_MODEL_ID=turbo ASR_DTYPE=float16 ASR_PORT=18101
python -m gpu_server
```

다른 터미널에서 같은 manifest를 사용한다.

```bash
python -m bench.asr_shadow_bench \
  --manifest bench/fixtures/asr-shadow/manifest.jsonl \
  --endpoint whisper-large-v3=http://127.0.0.1:18101 \
  --runs 3 \
  --output-dir results/asr-shadow-large-v3
```

출력은 다음 세 파일이다.

- `asr-shadow-raw.json`: 요청별 전사문, CER/WER, 지연, 안전 오류
- `asr-shadow-summary.json`: 모델별 집계와 P50/P95
- `asr-shadow-summary.csv`: 리뷰용 동일 집계

벤치 프로세스는 후보 한 요청이 실패해도 나머지를 계속 실행한다. 위험→안전
뒤집힘이 하나라도 있으면 종료 코드 2를 반환한다.

## 현장 코퍼스 승격 기준

`bench/fixtures/asr-shadow`는 개인정보 없는 합성 회귀 자료다. 운영 승격에서는
`tools/record_corpus.py`로 만든 비커밋 manifest를 입력하고 다음을 모두 만족해야 한다.

1. 최소 3명 × 핵심 10발화 × quiet/noisy × normal/weak = 120건
2. 위험→안전 뒤집힘 0건
3. 숫자·부정어·이동 가능/불가·긴급 표현 보존율 100% 목표
4. quiet CER ≤ 15%, SNR 10/5 dB CER ≤ 20/25% 목표
5. P95가 대화형 예산 이내이며 공유 GPU 부하 상태에서도 재현
6. 미달 또는 모델 간 핵심 슬롯 충돌 시 값을 확정하지 않고 `UNKNOWN` 및 관제 확인

Qwen3-ASR이 위 기준에서 미달하면 동일 HTTP 계약에서 large-v3-turbo로 되돌리고,
둘 다 미달하면 기존 Jetson 로컬 `small`을 유지한다.
