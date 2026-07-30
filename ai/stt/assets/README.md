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

전체 파일과 형식은 다음 명령으로 검사한다.

```bash
python -m tools.convert_guide_assets --source-dir . --force
python -m tools.validate_guide_assets --report results/guide-assets.json
```

실제 파일 목록과 녹음·장비 검증 절차는
[`../docs/README.md` §6 안내 음성 자산](../docs/README.md)를 따른다.
