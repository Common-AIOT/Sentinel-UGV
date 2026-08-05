# 블랙박스 소음 제거본 토글 — 실험판 (S15P11A301-202)

관제 블랙박스 화면에 **소음 제거본 토글**을 넣으면 어떻게 되는지 보여주는 참조
구현이다. 관제 화면은 프론트 담당 구역이라 [`../blackbox/page.tsx`](../blackbox/page.tsx)는
건드리지 않았다.

## 실행

```bash
npm run dev --prefix frontend
```

→ http://localhost:3000/blackbox-experiment

백엔드·S3 없이 뜬다([`mockApi.ts`](mockApi.ts)). 발견 이벤트를 누르면 영상이
재생되고 그 아래에 토글 버튼이 나온다.

### 샘플 미디어는 커밋되지 않는다

실육성이 들어 있어 개인 음성 커밋 금지 규정(`ai/voice/docs/README.md` §11-6)에
걸린다. `frontend/.gitignore`에 등록해 두었고, 직접 만들어 넣는다.

```bash
python denoise_try/make_demo_event.py                              # 샘플 이벤트 영상
python ai/voice/denoise/enhance_media.py <위에서 만든 mp4>                # 제거본
```

두 파일을 `frontend/public/experiment/`에 `event_sample.mp4`,
`event_sample-denoised.m4a`로 둔다. 없으면 토글은 뜨지만 재생이 안 된다.

## ⚠️ 이관 전에 diff를 먼저 확인한다

이 파일은 원본의 복사본이므로 **원본이 바뀌면 어긋난다.**

```bash
diff frontend/app/blackbox/page.tsx frontend/app/blackbox-experiment/page.tsx
```

`<` 쪽(원본에만 있는 줄)에 **토글과 무관한 것**이 나오면 이 복사본이 낡은 것이다.
그때는 원본을 다시 복사한 뒤 아래 블록만 얹는다.

> 실제로 한 번 어긋났다 — 2026-08-04 확인 시 S15P11A301-203이 넣은 `MissionMap`
> (임무 지도·발견 마커)이 이 복사본에 없었다. 그대로 diff를 뜨면 **토글을 넣는
> 변경이 지도를 지우는 것처럼** 보였다. 같은 날 원본 기준으로 다시 맞췄다.

## 실제 페이지와의 차이 — 이관할 부분

**두 가지만 다르다.**

| 차이 | 성격 |
|---|---|
| `import { mockApi as api } from "./mockApi"` (+ `useRef`·`Volume2`·`VolumeX` 추가) | **이관 대상 아님.** 백엔드 없이 띄우기 위한 것뿐 |
| `// ── 소음 제거본 토글` 주석이 붙은 블록 | **이관 대상** |

이관 대상 블록:

1. **상태** — `denoisedUrl` · `denoised` · `videoRef` · `audioRef`
2. **`openEncounter` 안** — 발견 전환 시 초기화 + 제거본 자산 조회
3. **동기 `useEffect`** — 재생·일시정지·탐색·배속 추종 + 드리프트 보정
4. **조작부** — `<audio>` 요소, 토글 버튼, 주의 문구. 영상 아래에 버튼을 붙이려고
   영상 `<div>`를 세로 묶음(`flex flex-col gap-2`)으로 감쌌다 — 이 감싸기도 함께 옮긴다
5. 헤더의 `실험판 · 목 데이터` 배지 — **이관 대상 아님**

## 목 데이터의 한계

`mockApi.ts`는 블랙박스 화면이 쓰는 5개 호출만 흉내낸다. **지도 관련 호출은 없다** —
그래서 임무 지도 자리에 "지도를 읽지 못했습니다"가 뜬다. 토글 검증에는 지장이 없으며
실제 페이지에서는 정상 동작한다.

## 왜 오디오를 따로 두는가

브라우저는 MP4 안의 오디오 트랙 전환을 지원하지 않는다(Chrome `audioTracks`
미지원). 그래서 영상은 그대로 두고 `<audio>`를 겹쳐 어느 쪽 소리를 낼지만 바꾼다.

영상을 두 벌 만드는 방법도 있지만 encounter당 약 94MB가 더 든다. 오디오만 올리면
64kbps로 5분에 약 2.4MB다.

제거본은 원본 오디오에서 파생되므로 **타임라인이 표본 단위로 일치한다.** 동기
코드는 재생 상태를 맞추기 위한 것이고, 0.15초 이상 벌어질 때만 보정한다.

## 실측 (개발 PC, 2026-08-03)

| 항목 | 값 |
|---|---|
| 토글 직후 드리프트 | 0.064초 |
| 탐색(seek) 후 | 0.018초 |
| 일시정지→재개 후 | 0.048초 |
| 일시정지 시 오디오 정지 | ✅ |
| 토글 해제 시 원본 복귀 | ✅ |

## 선행 조건 (프론트 밖)

1. **백엔드 `kind` 확장** — `EVENT_AUDIO_DENOISED`. `UploadUrlRequest` ·
   `MediaCompleteRequest`의 `@Pattern` 2곳, `MediaService.objectKey()` ·
   `contentType()`, 공통 스키마 2개
2. **서버 워커** — [`ai/voice/denoise/`](../../../ai/voice/denoise/). 젯슨에는 못 싣는다
   (aarch64 휠 부재)
3. **프라이버시 팀 합의** — 요구조자 음성이 로봇 밖으로 나가는 최초 경로

자산이 없으면 토글을 그리지 않으므로 **1·2가 끝나기 전에 프론트를 먼저 올려도
화면이 깨지지 않는다.**

## 주의 — 원본이 증거다

제거기는 사람 말만 남기므로 두드림·신음 같은 비언어 증거를 지운다. 그리고 실제
동시 녹음에서는 발화의 자음까지 깎는 것이 관측됐다(실측 문서 §9). 제거본은
명료도 보조이며 원본을 대체하지 않는다 — 화면에도 그 문구를 넣어 두었다.
