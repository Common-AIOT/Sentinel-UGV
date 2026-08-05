# 사전 녹음 안내 음성

이 디렉터리에는 `sentinel_voice.guide_audio.GUIDE_ASSETS`에 등록된 승인 WAV를 둔다.
실제 요구조자에게 재생되는 운영 자산이므로 문구나 파일을 임의로 추가하지 않는다.

필수 형식:

- WAV PCM 16-bit
- 16,000Hz
- mono
- 길이 0.3~15초
- peak -1dBFS 이하
- RMS -32~-12dBFS

자산은 팀에서 승인한 사전 녹음본을 사용한다. 운영 환경에서 동적 TTS를
생성하지 않으며, 문구를 바꾸면 WAV도 같은 변경에서 다시 녹음한다.

```bash
python -m tools.validate_guide_assets --report results/guide-assets.json
```

**문구를 바꿀 때는 자산도 같은 커밋에서 함께 바꾼다.** 코드가 새 문구로 대조하는데
스피커가 구 문구를 재생하면 에코 가드가 무력해진다(S15P11A301-165).

실제 파일 목록과 녹음·장비 검증 절차는
[`../docs/README.md` §6 안내 음성 자산](../docs/README.md)를 따른다.
