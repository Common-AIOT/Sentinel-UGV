# 다국어 TTS 모델 비교와 기존 자산 유지 결정 (S15P11A301-271)

측정일: 2026-08-05

장비: NVIDIA L40S 46GB(Device 3, 공유 GPU)

운영 결정: **기존 승인 WAV 6개를 변경 없이 유지**하고 런타임 TTS는 두지 않는다.
아래 모델 생성물은 비교 측정에만 사용하며 운영 자산과 저장소에 포함하지 않는다.

## 1. 후보와 라이선스

| 후보 | 크기·언어 | 라이선스 | 이번 판단 |
|---|---|---|---|
| [Qwen3-TTS](https://github.com/QwenLM/Qwen3-TTS) 0.6B/1.7B CustomVoice | 한국어 포함 10개 언어 | Apache-2.0 | 1.7B 기술 후보. 운영 미채택 |
| [Chatterbox Multilingual V3](https://github.com/resemble-ai/chatterbox) | 500M, 23개 이상 언어 | MIT | normal 후보 12/12 통과. 운영 미채택 |
| [Fish S2 Pro](https://huggingface.co/fishaudio/s2-pro) | 5B, 80개 이상 언어, 한국어 tier 2 | [Fish Audio Research License](https://huggingface.co/fishaudio/s2-pro/blob/main/LICENSE.md) | 승인 자산에서 제외 |

Fish S2 Pro를 기술적으로 실행할 수 없어서 제외한 것이 아니다. BF16 가중치만 단순
계산하면 약 10GB라 46GB L40S에서 단독 평가는 가능할 것으로 보인다. 다만 공식
성능 수치는 H200 기준이고, 현재 GPU는 다른 팀 프로세스와 공유 중이다. 무엇보다
연구·비상업 사용과 별도 상용 허가 조건이 있어 배포 자산의 출처로 선택하지 않았다.
교육용 비교가 필요하면 라이선스 문구·귀속 표시를 팀이 명시적으로 수용한 뒤 별도
실험으로 진행할 수 있다.

## 2. 재현 환경에서 발견한 호환성

- `qwen-tts==0.1.1` 기본 설치는 CUDA 13용 PyTorch를 받아 CUDA 12.8 드라이버에서
  GPU를 인식하지 못했다. 서버 드라이버는 건드리지 않고 `torch==2.7.1+cu128`,
  `torchaudio==2.7.1+cu128`로 고정했다.
- Chatterbox는 Python 3.12에서 `PerthImplicitWatermarker`가 `None`으로 로드돼
  초기화에 실패했다. 공식 권장과 같은 Python 3.11 격리 환경에서 정상화했다.
- 당시 PyPI 배포본은 V3 선택 인자를 노출하지 않았다. 공식 GitHub 소스를 Python
  3.11 환경에 설치하자 `t3_model="v3"`와 워터마커가 함께 정상 동작했다.

## 3. 깨끗한 음성 역-STT 결과

생성 WAV를 16kHz mono PCM16, RMS -20dBFS, peak -1.5dBFS 이하로 변환한 뒤
GPU의 Qwen3-ASR-1.7B가 다시 읽었다. CER 0.25 이하이면서 모든 `criticalTokens`를
보존해야 통과다.

| 모델·자산 | 통과 | 로드 | VRAM peak | 생성 P50 | 비고 |
|---|---:|---:|---:|---:|---|
| Qwen3-TTS 0.6B normal | 11/12 | 5.652s | 2,232MiB | 3.907s | 영어 `Can you move?`를 한국어로 오인해 탈락 |
| Qwen3-TTS 1.7B normal+slow, 최초 | 23/24 | 49.903s | 4,393MiB | 2.304s | 영어 재질문 normal 앞에 비문을 추가해 해당 자산 폐기 |
| Qwen3-TTS 1.7B, 재생성 후 | **24/24** | 위와 같음 | 위와 같음 | 위와 같음 | 정확한 문장만 말하도록 지시를 좁혀 재생성 |
| Chatterbox V3 normal | **12/12** | 35.659s | 3,219MiB | **1.054s** | 첫 생성 12.496s, 이후 약 1s. 워터마크 포함 |

Chatterbox가 normal 생성 속도와 메모리에서 앞선다. Qwen 1.7B는 별도 레퍼런스
음성 없이 한국어 기본 음색 `Sohee`를 양 언어에 고정하고 지시로 slow 후보를 만들 수
있었다. 그러나 제품은 문구가 6개로 고정되어 있고 기존 승인 WAV가 이미 있으므로,
모델을 교체해 얻는 운영상 이득보다 음색 변경과 재검수 비용이 크다. 따라서 두 후보
모두 기술 비교 결과만 남기고 운영에는 채택하지 않는다.

## 4. 숫자·부정어와 소음 게이트

운영 문구 외에 반대 의미로 뒤집히기 쉬운 문장을 별도 생성했다.

- 한국어/영어 × 부정(`없습니다`, `no`)·인원수(`세 명`, `three`) × normal/slow:
  **8/8 통과**
- 승인 자산 24개에 모터 120/240Hz 성분과 백색 소음을 SNR 5dB로 혼합:
  **23/24 통과**
- 위 소음 조건에서 slow만 집계: **12/12 통과**
- normal 실패 1건: 영어 INTRO의 `robot`을 `verbal`로 전사해 핵심 토큰 탈락

이 수치는 후보 모델의 기술 비교 결과이며 기존 운영 WAV의 승인 근거로 사용하지 않는다.
소음을 더한 WAV 역시 운영 자산으로 배포하지 않는다.

## 5. 운영 결정

- `assets/guide_*.wav` 기존 6개를 그대로 사용한다.
- 생성 모델, 다국어 자산, normal/slow 선택 로직을 운영 코드에 추가하지 않는다.
- 승인 WAV가 없거나 형식이 틀리면 기존처럼 재생을 거부한다.
- 런타임 동적 TTS를 호출하지 않는 기존 안전 경계를 유지한다.
- 향후 실제 다국어 요구가 확정되면 별도 Jira와 MR에서 문구 번역·사람 청취·현장
  명료도 검증을 모두 통과한 뒤 자산 교체를 다시 결정한다.

## 6. 생성물 보관 정책

GPU에서 만든 Qwen·Chatterbox WAV와 소음 혼합본은 비교용 임시 산출물이다. 기존
운영 음성과 혼동되지 않도록 저장소 및 배포 이미지에 포함하지 않는다. 재검증이
필요하면 아래 도구와 비운영 fixture로 다시 생성한다.

## 7. 재현 명령

```bash
python -m tools.generate_tts_candidates \
  --spec bench/fixtures/tts-critical-generation-spec.json \
  --output-dir /tmp/qwen-1.7b \
  --variants normal slow
python -m tools.generate_chatterbox_candidates \
  --spec bench/fixtures/tts-critical-generation-spec.json \
  --output-dir /tmp/chatterbox-v3
python -m tools.mix_tts_noise /tmp/qwen-1.7b/generation-report.json \
  --output-dir /tmp/qwen-noisy-snr5 --snr-db 5
python -m tools.score_tts_back_asr /tmp/back-asr-raw.json
```
