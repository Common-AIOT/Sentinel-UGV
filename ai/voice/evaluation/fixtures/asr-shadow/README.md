# ASR shadow smoke corpus

개인정보 없는 합성 음성으로 Qwen3-ASR과 Whisper 계열의 API·채점·안전 지표를
빠르게 검증하는 작은 코퍼스다. 한국어는 Windows `Microsoft Heami Desktop`, 영어는
`Microsoft Zira Desktop` 음성으로 생성했다. `silence.wav`는 16 kHz PCM16 3초 무음,
`noise-only.wav`는 고정 seed의 저진폭 백색 소음이다.

이 9건은 운영 모델을 최종 승인하는 실제 현장 코퍼스가 아니다. 합성 clean 발화는
사람·마이크·잔향·약한 목소리를 대표하지 못한다. 이 결과는 **서버 후보 제거와
회귀 스모크**에만 사용하고, 운영 승인은 `tools/record_corpus.py`로 수집한 최소
3명 × 10발화 × 4조건(120건)의 별도 개인정보 비커밋 코퍼스로 다시 실행한다.

`manifest.jsonl`의 주요 필드는 다음과 같다.

- `criticalTermGroups`: 한 그룹 안의 표현 중 하나라도 보존되면 성공이다.
- `expectedPolarity`: `risk`, `safe`, `unknown` 중 하나다.
- `riskPatterns`, `safePatterns`: 위험→안전 뒤집힘을 보수적으로 탐지하는 표현이다.
- `condition`: `silence`, `noise-only`는 전사문이 생기면 환각으로 센다.

실행 예시는 상위의 `docs/ASR-shadow-벤치마크.md`를 따른다.
