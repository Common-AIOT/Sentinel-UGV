# 사전녹음 안내 음성

이 디렉터리에는 `sentinel_voice.guide_audio.GUIDE_ASSETS`에 등록된 승인 WAV를 둔다.
실제 요구조자에게 재생되는 운영 자산이므로 문구나 파일을 임의로 추가하지 않는다.

필수 형식:

- WAV PCM 16-bit
- 16,000Hz
- mono
- 길이 0.3~15초
- peak -1dBFS 이하
- RMS -32~-12dBFS

MiniMax 원본(`ai/stt/mini_*.wav`, 커밋 대상 아님)을 변환해 만든다. 변환은 PyAV로
하며 ffmpeg 실행 파일이 필요하지 않다.

변환은 **시간 확장(`atempo=0.8`) → 라우드니스 정규화** 두 단계다. 원본이 너무 빨라서
(최고 8.2 음절/초) 늘린 것이며, 음높이는 바뀌지 않는다. 배율의 근거는
[`../docs/README.md` §6-2](../docs/README.md)에 있다(S15P11A301-260).

```bash
python -m tools.convert_guide_assets --source-dir . --force
python -m tools.validate_guide_assets --report results/guide-assets.json
```

**기본 배율이 커밋된 자산을 재현하는 값이다.** `--tempo 1.0`을 주면 원본 속도가 되며
그것은 커밋된 자산과 다르다. 배율을 바꾸려면 §6-2를 함께 고친다 — 두 곳이 어긋나면
누가 어느 값으로 만들었는지 알 수 없게 된다.

**문구를 바꿀 때는 자산도 같은 커밋에서 함께 바꾼다.** 코드가 새 문구로 대조하는데
스피커가 구 문구를 재생하면 에코 가드가 무력해진다(S15P11A301-165).

실제 파일 목록과 녹음·장비 검증 절차는
[`../docs/README.md` §6 안내 음성 자산](../docs/README.md)를 따른다.
