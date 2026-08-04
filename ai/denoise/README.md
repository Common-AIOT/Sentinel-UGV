# denoise — 블랙박스 오디오 잡음 제거 (서버측)

> Jira: S15P11A301-202 · 측정 근거: [`../stt/docs/measurements/잡음제거-실측.md`](../stt/docs/measurements/잡음제거-실측.md)

이벤트 영상(MP4)의 오디오 트랙에서 잡음을 제거해 **관제 대원이 듣는 용도**의
복원 음성을 만든다. 관제 블랙박스 화면에서 원본과 토글로 전환해 듣는다.

## 이 코드가 하지 않는 것 — 경계가 곧 설계다

| 하지 않는 것 | 근거 |
|---|---|
| **STT 입력에 사용** | 잡음 제거를 거치면 STT가 조용히 나빠진다. 108개 조건 실측에서 원본 58승 대 제거 16승, 환각 0.9%→13.9%. 제거기는 약한 자음(고역, 저에너지 프레임)을 소음으로 오인해 깎는데, 사람 귀에는 안 들리고 기계에는 치명적이다. 실측 문서 §3 |
| **젯슨에서 실행** | 의존 라이브러리 `DeepFilterLib`(Rust 확장)에 aarch64 휠이 없다. `packaging<24`·`numpy<2` 하드 핀이 젯슨 STT 환경과 충돌한다. 서버(EC2)에서 돌린다 |
| **원본 대체** | 제거본은 파생물이다. 원본이 항상 먼저 올라가고, 제거본이 없어도 블랙박스는 성립한다 |

## 흐름

```
이벤트 MP4 (H.264 + AAC 48kHz mono)
   │  extract_audio()        PyAV로 오디오 트랙만 디코딩
   ▼
float32 48kHz mono           DeepFilterNet 고유 샘플레이트 — 재샘플 없음
   │  denoise()              30초 조각 + 1초 크로스페이드 (RAM 상한 고정)
   ▼
제거된 48kHz mono
   │  write_m4a()            AAC 64kbps — 5분에 약 2.4MB
   ▼
event-denoised.m4a           관제 토글용 별도 자산
```

영상은 건드리지 않으므로 제거본 오디오의 타임라인은 원본과 표본 단위로 일치한다.
프론트는 `<video>`(원본)와 `<audio>`(제거본)를 겹쳐 두고 음소거만 전환한다.

### 오디오 없음과 무음은 다르다

`extract_audio()` 다음에 두 갈래로 멈춘다. **정상 경로와 장치 사망을 섞으면 안 된다.**

| 예외 | 언제 | 워커 상태 | 처리 |
|---|---|---|---|
| `NoAudioTrack` | 트랙이 아예 없다 | `NO_AUDIO` | **정상 경로.** 젯슨이 마이크를 못 열면 비디오만 기록한다 |
| `SilentAudioTrack` | 트랙은 있고 전 구간 peak 0 | `SILENT_AUDIO` | **장치 사망.** 업로드하지 않고 `[ALERT]`를 `scan.log`에 남긴다 |

무음을 잡음 제거해도 무음이고, 그것을 올리면 스캔이 그 발견을 완료로 표시해 **마이크
사망이 조용한 성공으로 덮인다.** 2026-08-04에 실제로 그렇게 리허설 영상 295초를 잃었다
(`../stt/docs/README.md` §9-8, S15P11A301-257). 업로드하지 않으면 스캔이 "rc 0인데 자산
없음"으로 보고 skip 처리하므로 무한 재스캔도 나지 않는다 — `NO_AUDIO`와 같은 취급이다.

## 사용

### 파일 하나 처리 (검증·청취용)

```bash
pip install -r requirements.txt
python enhance_media.py event.mp4                    # event-denoised.m4a 생성
python enhance_media.py event.mp4 -o out.m4a
python enhance_media.py event.mp4 --wav              # 검청용 WAV도 함께
python enhance_media.py event.mp4 --atten-lim-db 12  # 과잉 억제 억제
```

첫 실행 시 DeepFilterNet3 모델(약 9MB)을 내려받아 캐시한다.

### 관제에 등록 (운영)

```bash
python worker.py --encounter <encounterId> --api http://backend:8080
python worker.py --encounter <id> --dry-run          # 만들기만, 업로드 없음
```

[`worker.py`](worker.py)가 encounter 하나를 처리한다 — 영상을 내려받아 제거본을
만들고 media API로 등록한다. **S3 자격증명을 갖지 않는다**: 다운로드는
`view-url`, 업로드는 `uploads`가 발급하는 Presigned URL로 하며 둘 다 백엔드가
서명한다.

`mediaId`는 영상 `mediaId`에서 uuid5로 파생하므로 재시도해도 같다(멱등, 31-10).
이미 `AVAILABLE`이면 아무것도 하지 않는다.

**상주시키지 않는다.** 한 건 처리하고 죽는다 — 상주하면 torch가 평소 700MB를
점유하는데, 처리는 1~2분 영상에서 1~3초다. 종료 코드로 재시도 여부를 구분한다:
`0` 성공 · `1` 재시도 가능 · `2` 재시도 무의미(선행 조건 미충족 등).

> ⚠️ **백엔드 선행 작업이 필요하다.** `EVENT_AUDIO_DENOISED` kind를
> `UploadUrlRequest`·`MediaCompleteRequest`의 `@Pattern`과
> `MediaService.objectKey()`·`contentType()`, 공통 스키마 2개에 추가해야 한다.
> 그 전에는 5단계가 400이며 워커가 `KIND_NOT_SUPPORTED`로 알려준다.

## 감쇠 상한 — 실제 녹음에서는 기본값이 과할 수 있다

합성 혼합 시험에서는 안 드러났는데, **실제 마이크로 목소리와 소음을 동시에 녹음한
파일**에서는 무제한 감쇠가 발화의 자음까지 지웠다(1k~3.4kHz 잔존 5.9%). 모델이
가산 혼합으로 학습됐기 때문에 합성 시험이 유리했던 것이다(실측 문서 §9).

```bash
python enhance_media.py event.mp4 --atten-lim-db 12   # 감쇠를 12dB로 제한
```

값은 관제 청취 판정으로 확정한다 — 사람 귀가 소비자이므로 지표가 아니라 귀가 기준이다.
10~15dB가 후보이며 `ATTEN_LIMIT_DB` 상수에 실측표를 적어 두었다.

## 조각 처리를 하는 이유

`enhance()`는 입력 전체를 한 번에 처리해 RAM이 길이에 비례한다(120초에 약 1.3GB
실측). 30초 조각으로 자르면 상한이 고정된다. 조각 경계의 불연속은 1초 겹침
구간을 선형 크로스페이드로 이어 지운다.

## torchaudio 호환 shim

DeepFilterNet 0.5.6은 torchaudio 2.11에서 삭제된 `torchaudio.backend.common`을
import한다. [`torchaudio_compat.py`](torchaudio_compat.py)가 그 모듈만 복원한다.
환경의 torchaudio 자체는 건드리지 않는다.

## 검증

```bash
pytest tests/ -v
```

무거운 모델이 없어도 추출·인코딩 경로는 검증되도록, DeepFilterNet 필요 테스트는
`importorskip`으로 분리했다.
