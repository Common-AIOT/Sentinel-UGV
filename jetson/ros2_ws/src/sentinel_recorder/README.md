# sentinel_recorder

링 버퍼 조각을 모아 이벤트 MP4를 만듭니다 (S15P11A301-123).

명세 **32-5**가 규범입니다. 저장 방식과 조각 인덱스, 이벤트 시작·종료 절차, 녹화
상태 머신, 종료 예외를 그대로 따릅니다.

## 두 프로세스로 나눈 이유

```text
sentinel_streaming (파이프라인)        sentinel_recorder (이 패키지)
  x264enc → tee                          index.json 읽기
       ├─ 스트리밍                        조각 hard link
       └─ splitmuxsink                    녹화 상태 머신
          1초 MPEG-TS 조각                MP4 생성·검증·SHA-256·썸네일
          index.json 기록                 pending 상한 관리
                     └──── 경계 ─────┘
```

완료 조건이 **"녹화를 인위적으로 실패시켜도 관제 스트리밍과 AI가 유지된다"**
입니다. 같은 프로세스면 MP4 생성 실패나 디스크 오류가 GStreamer 파이프라인
재구성을 유발해 스트리밍까지 끊깁니다. `index.json`을 경계로 두면 이 노드를
`kill -9`해도 스트리밍은 모릅니다.

`splitmuxsink`는 `tee`에 달려야 하므로 파이프라인 프로세스에 있을 수밖에
없습니다. 나눌 수 있는 것만 나눴습니다.

## 실행

```bash
ros2 launch sentinel_recorder recorder.launch.py
```

링 writer를 먼저 켜야 조각이 생깁니다.

```bash
ros2 launch sentinel_streaming streaming.launch.py enable_record_branch:=true
```

이 노드는 `index.json`만 읽으므로 스트리밍보다 먼저 떠도 되고 나중에 떠도
됩니다. 파일이 없으면 조용히 기다립니다.

## 트리거

`/perception/encounter`의 `std_msgs/String`에 담긴 JSON입니다. 계약은
[`common/schemas/encounter.schema.json`](../../../../common/schemas/encounter.schema.json)
이며 CI의 `test:message-contract`가 검증합니다.

`phase`만 보고 전이합니다. 사람 수와 위치는 보고서용입니다.

| phase | 하는 일 |
|---|---|
| `CONFIRMED` | `BUFFERING` → `RECORDING`. 확정 직전 3초를 가져온다 |
| `APPROACHED` | `RECORDING` → `INTERACTION` |
| `ENDED` | → `POST_RECORDING` |
| `REDETECTED` | 사후 3초 안이면 `INTERACTION`으로 되돌린다 |
| `LOST` | 종료 절차로 보낸다 |

**커스텀 ROS 메시지 패키지를 만들지 않았습니다.** AI 패키지가 빌드 의존을 걸어야
하고, JSON은 `common/schemas`와 CI라는 검증 장치를 이미 갖고 있습니다.
`sentinel_streaming`도 `~/status`·`~/segment_boundary`에 같은 방식을 씁니다.

## AI 없이 검증하기

탐지 노드(S15P11A301-43)가 아직 없으므로 트리거 도구로 같은 신호를 만듭니다.

```bash
# VID-03  사전 3초 확인
ros2 run sentinel_recorder trigger_encounter --scenario short

# VID-04  60초 상호작용이 한 파일로 나오는지
ros2 run sentinel_recorder trigger_encounter --scenario interaction --seconds 60

# VID-05  사람 3명이 encounter 1개, MP4 1개인지
ros2 run sentinel_recorder trigger_encounter --scenario multi-person --persons 3

# 낱개 신호
ros2 run sentinel_recorder trigger_encounter --phase CONFIRMED
```

## 산출물

```text
pending/<encounterId>/
├─ event.mp4        재생 검사를 통과한 것만 이 이름을 갖는다
├─ thumbnail.jpg    확정 시점에서 뽑는다
└─ report.json      영상이 없어도 남는다
```

`report.json`의 `coverage`로 VID-03과 VID-04를 판정합니다.

```json
"coverage": { "preRollSeconds": 3.37, "postRollSeconds": 5.65 }
```

**로그 문구로 판정하지 마세요.** 이벤트 시작 시점의 `index.json`에는 아직 열려
있는 조각이 없어 초기 수집이 최대 1조각 짧게 잡힙니다. 그 조각은 닫히는 즉시
따라붙으므로 최종 파일은 정확하지만, 로그의 "사전 N초"는 과소 보고입니다.

`uploadState`는 `UPLOAD_PENDING`으로 시작합니다. S15P11A301-124가 업로드에
성공하면 `AVAILABLE`로 바꿉니다. 이 패키지는 그 값을 읽기만 합니다.

## 마감 실패는 관제까지 나갑니다 (S15P11A301-309)

`mediaState`가 `report.json`에만 있던 동안, 마감 실패는 **젯슨 디스크 밖으로 나간
적이 없었습니다.** 표시가 빠진 것이 아니라 전송이 없었습니다 — `ready_for_upload`가
`has_media and has_checksum`이라 `event.mp4`가 없는 실패 이벤트는 업로드 경로를
아예 타지 않습니다. 그래서 관제 화면에는 「영상 없는 발견」으로만 보였고,
S15P11A301-304의 PTS 동률 결함은 19건이 쌓일 때까지 드러나지 않았습니다.

`~/status`가 마감 결과를 함께 싣습니다. `cloud_bridge`가 이것을 텔레메트리
`health.recorderOk`·`health.recorderLastFailure`로 옮깁니다.

```json
{"state": "IDLE", "lastFinalizeOk": false,
 "lastFailure": "RECORDING_FAILED_PTS_REGRESSION", "at": "..."}
```

**성공은 `lastFailure`를 지우지 않습니다.** 간헐 실패는 성공 사이에 섞여 들어오므로,
성공 한 번에 지우면 화면에서 사라집니다. 「지금 정상인가」는 `lastFinalizeOk`가
답하고 「이번 기동에 실패가 있었나」는 `lastFailure`가 답합니다. 판정 규칙과 그
근거는 [`recording_health.py`](sentinel_recorder/recording_health.py) 모듈 주석에
있습니다.

부팅 복구가 찾은 `CORRUPT`는 사유만 남기고 `lastFinalizeOk`는 건드리지 않습니다.
지난 기동의 잔해이지 이번 기동의 마감 결과가 아니기 때문입니다.

백엔드 적재와 화면 표시는 아직 없습니다(S15P11A301-309에 위임).

## 영상 내용을 검증하는 방법

**길이와 프레임 수만 보면 안 됩니다.** 실제로 그것 때문에 심각한 결함을 놓쳤습니다.

조각을 이벤트 디렉터리로 가져올 때 링 버퍼의 파일명을 그대로 쓴 적이 있습니다.
`splitmuxsink`가 `max-files`로 파일명을 순환시키므로 서로 다른 조각 수십 개가 같은
8개 파일을 가리켰고, **MP4가 같은 8초를 반복**했습니다.

그런데 68조각 이벤트가 67.9초 2036프레임으로 나왔습니다. 30fps 계산까지 정확히
맞아서 정상으로 판정했습니다.

여러 시점의 프레임을 뽑아 해시를 비교하면 잡힙니다.

```bash
MP4=pending/<encounterId>/event.mp4
for t in 2 10 18 26 34 42; do
  ffmpeg -hide_banner -loglevel error -ss $t -i "$MP4" -frames:v 1 -q:v 5 /tmp/f_$t.jpg -y
done
sha256sum /tmp/f_*.jpg | awk '{print $1}' | sort -u | wc -l   # 뽑은 개수와 같아야 한다
```

패킷 PTS 단조성도 함께 봅니다.

```bash
ffprobe -v error -select_streams v:0 -show_entries packet=pts_time \
        -of csv=p=0 "$MP4" | python3 -c "
import sys
ts=[float(l.strip().rstrip(',')) for l in sys.stdin if l.strip().rstrip(',')]
print('역행', sum(1 for a,b in zip(ts,ts[1:]) if b<=a), '건')"
```

## MP4를 만드는 순서 (32-5)

```text
조각 시각·PTS 순서 검증 → 누락 검사 → 스트림 복사 재다중화
→ .partial 생성 → 재생 검사 → SHA-256 → 원자적 rename → 썸네일
```

`.partial`을 쓰는 이유는 **검사를 통과한 파일만 최종 이름을 갖게** 하려는
것입니다. 부팅 후 `.partial`이 보이면 그것은 실패한 것입니다.

재생 검사에 `-count_packets`를 씁니다. `-count_frames`는 전부 디코딩하므로 5분
영상에서 수십 초가 걸립니다(S15P11A301-62에서 18000프레임으로 timeout에
걸렸습니다). `alignment=au`이므로 패킷 하나가 프레임 하나입니다.

썸네일을 사전 영상 길이만큼 들어간 지점에서 뽑습니다. 파일 첫 프레임은 사람이
확정되기 전이라 빈 복도일 수 있습니다.

## 오디오 (S15P11A301-131)

32-5의 "H.264/AAC 재다중화"대로 이벤트 MP4에 AAC 트랙이 들어갑니다. 오디오를
넣는 것은 `sentinel_streaming`의 링 writer이고(`ring.audio_0` pad), 여기서는
조각에 이미 들어 있는 AAC를 `-c copy`로 그대로 옮깁니다. 재인코딩하지 않습니다.

`usb_cam`은 오디오를 발행하지 않습니다. 소리는 ROS 토픽을 거치지 않고
GStreamer 안에서 `pulsesrc`로 직접 들어옵니다. 그래서 녹화 노드가 오디오를
다루는 코드는 없고, 확인만 합니다.

**오디오가 없어도 실패가 아닙니다.** 마이크가 없거나 열리지 않으면 스트리밍
노드가 오디오를 끄고 비디오만으로 파이프라인을 다시 세웁니다. 그 경우 보고서의
`media.audio`가 `null`이고, 관제는 파일을 열지 않고도 "소리가 있는 이벤트인가"를
판정할 수 있습니다.

```json
"media": {
  "path": "event.mp4",
  "sha256": "...",
  "audio": { "codec": "aac", "sampleRate": 48000, "channels": 1,
             "durationSeconds": 300.011 }
}
```

마이크는 확정되지 않았습니다(TBD-AUD-001). BRIO 100 내장 마이크가 잠정이고
STT 인식률이 미달하면 USB 마이크로 바꿉니다. 바꿀 때 고치는 것은
`media.yaml`의 `audio_source` 한 줄입니다.

## pending 상한

TBD-VID-001에서 정한 **30분 분량**입니다. 비디오 2500kbps에 오디오 몫을 더해
약 580MB입니다(S15P11A301-131).

오디오 몫은 인코더 설정값이 아니라 실측값을 씁니다. `voaacenc bitrate=64000`인데
디스크 증가분은 72.7kbps입니다.

```text
30초 클립 실측
  비디오만        v_only.ts   2,758,524B     v_only.mp4   2,490,458B
  비디오+오디오   v_plus_a.ts 3,031,500B     v_plus_a.mp4 2,763,019B
  증가분          TS +72.8kbps               MP4 +72.7kbps
```

차이는 AAC 프레임 헤더와 다중화 몫입니다(약 13%). 설정값 64를 그대로 쓰면 상한을
과소 계산하고, 상한을 과소 계산하면 **지울 필요가 없는 미업로드 이벤트를
DISK_FULL로 포기합니다.** 그래서 `audio_bitrate_kbps`를 80으로 두어 여유를
둡니다. 30분 상한이 562MB에서 580MB가 되고, microSD 여유 13GB의 4.5%입니다.

오디오를 끈 구성에서는 `audio_bitrate_kbps: 0`으로 내립니다. 그러지 않으면 상한을
과대 계산해 지워도 되는 것을 남깁니다.

```text
1단계  업로드 완료분의 영상을 오래된 순으로 삭제 (보고서·썸네일은 남긴다)
2단계  그래도 넘으면 업로드 완료 디렉터리를 통째로 삭제
3단계  미업로드분만으로 넘으면 영상을 포기하고 RECORDING_FAILED_DISK_FULL
```

**영상을 포기해도 썸네일과 보고서는 남깁니다.** 32-5가 명시했습니다. 관제에서
"이 시각에 사람을 발견했으나 영상이 없다"를 볼 수 있어야 하고, 그것은 영상보다
훨씬 작습니다.

디스크를 채우는 것은 링 버퍼가 아니라 pending입니다. 링 버퍼는 1초 조각 8개를
순환하므로 점유량이 약 2.5MB로 고정됩니다. 2.5Mbps는 시간당 1.1GB의 쓰기
*처리량*이지 누적량이 아닙니다.

## 한 encounter는 한 번만 녹화합니다 (S15P11A301-142)

이미 마감한 `encounterId`로 `CONFIRMED`가 다시 오면 무시합니다.

### 왜 그 경로가 생기는가

`mission_manager`는 사람이 추적되는 동안 같은 `encounterId`로 `CONFIRMED`를 계속
발행합니다. 그런데 이 노드의 마감 조건(`NO_RESPONSE_TIMEOUT`, `MAX_DURATION`,
`PERSON_LOST`)은 `mission_manager`의 상태와 독립입니다. 그래서 우리가 먼저 마감한 뒤
오는 `CONFIRMED`가 같은 `encounterId`로 새 녹화를 시작했습니다.

### 왜 그냥 두면 안 되는가

두 녹화의 `mediaId`는 다른데 백엔드가 만드는 object key는 같습니다. 29.6이 key에
`encounterId`까지만 쓰기 때문입니다.

```text
missions/{missionId}/encounters/{encounterId}/event.mp4
```

`media_assets.s3_key`가 UNIQUE라 두 번째 발급이 유니크 제약을 위반하고 **500으로
영구 실패**합니다. 재시도는 망 단절 복구(VID-06)를 위해 무한이므로, 그 이벤트가
30초마다 백엔드를 두드리며 pending 상한까지 점유합니다.

실측에서 이렇게 나왔습니다.

```text
6a75f497-...      mediaId=dc81c239-...
6a75f497-..._2    mediaId=73f6d1bb-...
→ media_uploader: PRESIGN_SERVER_ERROR 500 (무한 반복)
```

### 왜 무시가 맞는가 — 그리고 무엇을 포기하는가

마감했다는 것은 `REPORT_COMMITTED`를 이미 보냈다는 뜻이고, 그 발견은 보고가 끝난
상태입니다. 32-5가 이벤트 길이를 5분으로 제한한 것 자체가 "한 발견 = 한 파일"을
전제한 것으로 읽힙니다.

**대가가 있습니다.** 마감 이후에도 머문 사람의 뒷부분 영상은 남지 않습니다. 다른
선택지는 이랬습니다.

| 선택 | 결과 | 왜 택하지 않았나 |
|---|---|---|
| **마감된 encounter 무시** | 뒷부분 영상 없음 | 택함 |
| 새 encounter로 취급 | 발견 2건으로 기록 | 같은 사람이 2명으로 집계된다 |
| 첫 파일에 이어붙임 | 영상 온전 | 32-5의 5분 상한을 우회한다 |

뒤 두 개는 **잘못된 기록**을 만듭니다. 영상 뒷부분을 잃는 것이 기록을 틀리게 하는
것보다 낫다고 판단했습니다.

음성 상호작용이 들어와 `last_activity_at`이 실제로 갱신되면 대화가 5분 안에 끝나므로
이 대가는 대부분 사라집니다. 지금은 그 값을 갱신하는 경로가 없어
`no_response_timeout_seconds`를 `max_event_seconds`와 같게 두었습니다(recorder.yaml
주석 참고).

### `_2`는 부팅 복구 전용입니다

이 노드가 살아 있는 동안 마감한 encounter는 위 가드가 걸러내므로 `_2` 경로에 오지
않습니다. 거기까지 왔다면 완성 영상이 **이전 프로세스가 남긴 것**이라는 뜻입니다 —
노드가 재시작됐고 `mission_manager`는 같은 encounter를 아직 진행 중으로 아는
경우입니다.

그때는 덮어쓰지 않고 이름을 비켜 씁니다. 이전 영상은 아직 업로드되지 않았을 수 있고
그것을 잃으면 발견 기록이 사라집니다. 이 경로에서는 여전히 key가 충돌하지만,
재시작은 드물고 **잘못된 실패는 고칠 수 있지만 지워진 영상은 복구할 수 없습니다.**

그 충돌의 근본 해결은 백엔드 몫입니다 — 같은 key 재발급을 멱등하게 처리하거나 409로
내려야 합니다(S15P11A301-142의 백엔드 항목).

### 재시도 상한은 두지 않았습니다

"N번 실패하면 영구 실패"로 막고 싶어지지만 하면 안 됩니다. `retryable` 오류에 망
단절이 포함되고, VID-06(망 단절과 복구)이 그 무한 재시도에 의존해 통과했습니다.
상한을 걸면 긴 단절에서 이벤트가 영구 실패로 굳어, 재난 현장 Wi-Fi 단절을 전제로
만든 설계가 무너집니다. 500과 망 오류를 횟수로 구분할 수 없으므로 그 방향은 막힌
길입니다.

## 지도 저장 (S15P11A301-171)

임무가 끝나면(`/mission/status`의 `COMPLETED`) SLAM 지도를 로컬에 남깁니다.

```text
/var/lib/sentinel/maps/<missionId>/map.pgm      점유격자
                                  /map.yaml    해상도·원점
                                  /report.json 업로드 대기 표시
```

31-10이 "업로드 대기 영상·**지도**"를 로컬 보존 대상으로 정했습니다. 망이 끊긴 채
임무가 끝나도 지도를 잃지 않습니다.

**업로드는 아직 하지 않습니다.** 지도 업로드 API가 백엔드에 없습니다(2026-07-30
확인, Swagger에 maps 엔드포인트 0건). `report.json`의
`uploadState: UPLOAD_PENDING`이 그 경계이고, API가 생기면 이 디렉터리를 훑는
업로더만 붙이면 됩니다 — 저장 코드는 바뀌지 않습니다.

`missionId`가 없으면 `no-mission/`에 저장합니다. 백엔드 `maps` 행은 만들 수 없지만
(`mission_id`가 NOT NULL FK) 개발 중 관제 없이 젯슨만 띄운 지도도 사람이 열어볼
값이 있습니다.

### 저장이 실패하면 다시 부릅니다

`save_map`은 slam_toolbox 안의 lifecycle map_saver가 처리하고, 그것은 `/map`을
**2초만 기다립니다**(nav2 map_io 기본값). 우리 `/map`은
`slam_toolbox.yaml`의 `map_update_interval: 2.0` 때문에 2초 주기라 두 값이 정확히
맞물립니다. 실측에서 한 번은 잡히고 한 번은 놓쳤습니다.

```text
[map_saver]: Failed to spin map subscription    ← 놓친 경우, result=255
```

그래서 실패하면 2.5초 뒤 다시 부릅니다(최대 4회). 발행 주기를 한 번 건너뛰면
잡힙니다. `map_update_interval`을 낮추는 방법도 있지만 SLAM의 CPU를 상시로 더 쓰고,
저장은 임무당 한 번뿐이라 재시도가 값이 쌉니다.

서비스 응답 코드만 믿지 않고 **파일을 직접 확인**합니다. 성공 코드가 왔는데 파일이
없는 경우를 이벤트 썸네일에서 겪었습니다(S15P11A301-131).

### 검증 (2026-07-31)

관제 START → 탐사 → STOP 전 구간을 실제 임무·실제 백엔드로 돌렸습니다.

```text
missionId   4355aefb-78a6-4c3b-837e-f9ecea85f052 디렉터리에 저장
파일        map.pgm 63,455B · map.yaml 121B · report.json 346B
지도        244x260셀 = 12.2m x 13.0m, 해상도 0.05
            점유 1.2% · 자유 14.9% · 미지 83.9%
uploadState UPLOAD_PENDING
```

pgm을 열어 실제 복도 형상이 보이는 것까지 확인했습니다.

첫 검증에서 `missionId`가 `no-mission`으로 저장됐습니다. `mission_state`의
`MISSION_COMPLETED` 처리가 전이 **전에** `mission_id`를 지워(S15P11A301-143)
발행되는 상태에 이미 null이 실렸기 때문입니다. 지우던 근거는
`observe_candidates`가 이미 막고 있어(EXPLORING이 아니면 새 encounter를 만들지
않음) 이중 방어가 정보만 잃는 셈이었으므로, 지우지 않도록 고쳤습니다.

## 지도 업로드 (S15P11A301-171 후반부)

`map_uploader`가 디렉터리를 주기적으로 훑어 `uploadState`가 `AVAILABLE`이 아닌
지도를 올립니다. 백엔드 계약은 S15P11A301-185입니다.

```text
POST /api/v1/maps/uploads                    { missionId }
  → { mapId, pgmKey, yamlKey, pgmUrl, yamlUrl, contentType, expiresInSec }
PUT  <pgmUrl>, <yamlUrl>                     presigned, 스토리지 직접
POST /api/v1/maps/uploads/{mapId}/complete
```

저장 노드와 별 프로세스입니다(32장 장애 격리). 업로드가 망 때문에 막혀도 저장은
계속되고, 업로더가 죽어도 파일이 남아 다음 기동에서 이어받습니다. 저장 완료를
토픽으로 받지 않고 폴링하는 이유가 그것입니다 — 프로세스가 죽은 사이에 저장된
지도와 망이 끊겼던 동안 쌓인 지도를 **재기동만으로** 집어야 합니다(31-10).

`no-mission` 디렉터리는 올리지 않습니다. `maps.mission_id`가 NOT NULL FK라
등록할 수 없습니다. 파일은 남겨 사람이 열어볼 수 있게만 합니다.

### 백엔드 주소를 틀리면 망 문제로 보이지 않습니다

`backend_base_url`에 apex 도메인(`sentinel-ugv.xyz`)을 쓰면 모든 요청이 404가
됩니다. 그곳은 Vercel 프론트입니다. **프론트가 200을 주므로 연결이 안 되는
것처럼 보이지도 않습니다.** API는 `api.sentinel-ugv.xyz`입니다.

`media_uploader`의 기본값도 apex였고 `demo.launch.py`가 손으로 덮어쓰고
있었습니다. 지도 업로더가 같은 함정을 밟았으므로 두 기본값을 모두 API 호스트로
올렸습니다.

### 검증 (2026-07-31, 실제 백엔드)

```text
성공        mission 4355aefb-… → mapId c6522cf7-d323-410b-ad63-c8feb3544872
            keys missions/<missionId>/maps/<mapId>/map.{pgm,yaml}
            report.json: AVAILABLE + mapId + keys + sha256
중복 방지   폴링을 계속 돌려도 등록 1회 (AVAILABLE이면 건너뜀)
임무 없음   404 MISSION-001 → 재시도하지 않는 실패로 기록
망 단절     PRESIGN_UNREACHABLE 5회, PENDING 유지, 파일 보존, mapId 없음
복구        같은 디렉터리로 재기동 → 집어서 등록, mapId 963f4e8f-…
토픽        /map_uploader/registered  {"missionId": …, "mapId": …}
```

업로드가 실제로 스토리지에 닿았다는 근거는 백엔드 쪽에 있습니다.
`MapUploadService.completeUpload`가 `s3Client.headObject`로 객체 존재를
검증하고 없으면 `MAP_UPLOAD_INCOMPLETE`를 던집니다. 완료가 성공했다는 것이
객체가 있다는 뜻입니다.

sha256은 계약에 없습니다(`MapUploadRequest`는 `missionId` 하나). 그래도 계산해
`report.json`에 남깁니다 — 나중에 객체가 깨진 것으로 의심될 때 우리 쪽에 비교할
값이 없으면 확인할 방법이 없습니다.

### 남은 것 — mapId 수명주기

`mapId`가 등록됐지만 **telemetry·encounter의 mapId는 아직 이 값을 쓰지
않습니다.** 시점이 맞지 않습니다.

```text
임무 중    telemetry·encounter가 mapId를 필요로 한다
임무 종료  이때 지도가 등록되고 진짜 mapId가 생긴다
```

같은 임무 안에서는 앞의 것이 뒤의 것을 참조할 수 없습니다. 그래서
`cloud_bridge`는 SLAM 세션마다 자체 UUID를 쓰고(S15P11A301-137),
`encounter.pose.mapId`는 null입니다(S15P11A301-170).

푸는 방법은 둘입니다.

| 방법 | 대가 |
| --- | --- |
| 발급 요청이 클라이언트가 만든 `mapId`를 받아준다 | 백엔드 변경 필요. 젯슨이 SLAM 세션 시작 때 UUID를 만들어 처음부터 일관되게 쓸 수 있다 |
| 등록 후 `/map_uploader/registered`를 받아 소급 연결한다 | 백엔드 변경 없음. 임무 중 발행된 값은 여전히 다른 UUID이므로 관제가 두 값을 이어야 한다 |

첫 번째가 옳지만 백엔드 합의가 필요합니다. 지금은 `registered` 토픽을
TRANSIENT_LOCAL로 발행해 두어 어느 쪽으로 가든 소비할 수 있게만 했습니다.

## 부팅 복구

`.partial`이 남아 있으면 지우고 `CORRUPT`로 표시합니다. **재다중화를 다시
시도하지 않습니다.** 조각이 이미 사라졌을 수 있고, 남아 있어도 왜 검사에
실패했는지 알 수 없습니다. 깨진 영상을 업로드하는 것보다 깨졌다고 기록하는 편이
낫습니다.

## 검증 기록 (2026-07-28)

```text
VID-03  사전 영상 3.32초                    기준 3초 이상, 허용오차 +1초
VID-04  40초 상호작용 → 47.0초 1409프레임    조각 48개 연속, 한 파일
VID-05  확정 3회 → encounter 1개 MP4 1개     personCount 3
VID-07  SIGKILL 후 재시작 → 조각으로 복구     보고서 없으면 CORRUPT
장애격리  recording_manager kill -9           스트리밍 생존, WHEP 204
내용검증  6개 시점 프레임 해시 전부 다름        패킷 PTS 역행 0건
VID-06  백엔드 차단 3건 → 로컬 보존 → 복구    MinIO 6객체, 중복 업로드 0
VID-09  상한 초과 → 완료분부터 삭제 → 거부     DISK_FULL, 썸네일·보고서 보존
단위시험  상태 머신·보고서 원자성 21건
```

### VID-06 망 단절과 복구 (S15P11A301-124)

백엔드를 정지시킨 상태에서 이벤트 3건을 녹화하고, 업로더를 `SIGKILL`한 뒤 백엔드를
살려 재시작했습니다. 재시작을 끼운 것은 `AttemptState`가 메모리에만 있어서
프로세스가 죽으면 백오프가 사라지는데, 그때도 이벤트를 잃지 않는지 봐야 하기
때문입니다.

```text
차단 중       로컬 3건 UPLOAD_PENDING, MinIO 0객체, PRESIGN_UNREACHABLE 15회
              영구 실패 0회  ← 여기가 결함이 있던 자리
업로더 SIGKILL 로컬 3건 그대로
복구 후       3건 모두 AVAILABLE, MinIO 6객체(영상3+썸네일3)
              presign 6회, complete 6건, 중복 complete 0건
```

중복 업로드는 객체 수와 스텁이 받은 호출 수를 함께 세서 확인했습니다. 객체 수만
보면 같은 키에 두 번 올려도 6개로 보입니다.

### VID-09 pending 상한 (S15P11A301-124)

상한을 25초(약 7.8MB)로 줄여 3단계 전부를 타게 했습니다. 당시 실제 상한
562MB(오디오 추가 후 580MB)에서 이벤트 180여 건을 녹화하는 것과 같은 경로입니다.

```text
1단계  업로드 완료분의 event.mp4 삭제      2.8MB, 3.1MB  보고서·썸네일 유지
2단계  업로드 완료 디렉터리 통째 삭제       0.1MB 2건
3단계  RECORDING_FAILED_DISK_FULL         미업로드분은 지키고 새 영상을 포기
       썸네일 39~40KB 1280x720, 보고서 남음
```

미업로드 이벤트는 한 건도 지워지지 않았습니다. 32-5의 "미업로드분만으로 상한을
넘으면 새 영상을 포기한다"가 그 뜻입니다.

`incoming_bytes` 추정이 실제보다 큽니다. 마무리 전 조각 사본이 이미 `pending`에
있는데 거기에 MP4 예상 크기를 또 더하기 때문입니다. 과대 추정은 안전한 방향입니다
— 이벤트를 잃는 것이 아니라 이미 업로드된 것을 조금 일찍 지웁니다. 580MB 상한에서
3MB 차이는 무의미하므로 고치지 않았습니다.

### 이 두 시험에서 잡은 결함

세 개 모두 "이벤트 영상을 영원히 잃는" 부류였고, 숫자만 보는 검증으로는 안 잡혔습니다.

`report.json`을 비원자적으로 썼습니다. `recording_manager`와 `media_uploader`는 별
프로세스이고 같은 파일을 씁니다. 겹치는 순간 잘린 JSON을 읽고, 그 이벤트가
`REPORT_UNREADABLE`로 영구 실패 처리돼 다시는 업로드되지 않았습니다.
`write_report`가 임시 파일 + `fsync` + `os.replace`를 씁니다. MP4는 이미 그렇게 하고
있었는데 보고서만 빠져 있었습니다.

마무리 중인 이벤트를 업로드 대상으로 잡았습니다. 32-5의 순서상 `event.mp4`는 최종
이름인데 보고서에 `media.sha256`이 아직 없는 창이 있습니다. 그 창에 걸린 이벤트가
`CHECKSUM_MISSING`으로 영구 실패했습니다. `PendingEvent.ready_for_upload`가
체크섬 유무를 함께 봅니다.

DISK_FULL일 때 썸네일이 없었습니다. 로그는 "썸네일과 보고서는 남긴다(32-5)"라고
말하는데 실제로는 보고서만 남았습니다. 썸네일을 MP4에서 뽑기 때문이고, MP4를
포기했으니 만들 수 없었습니다. 이제 조각에서 직접 뽑습니다. `-ss`를 주면 안 됩니다
— TS에는 정확한 duration 헤더가 없어 1초 조각에서 입력 탐색이 실패하고, ffmpeg가
**오류 메시지 없이 빈 파일**을 만듭니다.

### PoC-B 조건 6 (조각 누락 0)

180초 상시 쓰기 실측입니다.

```text
조각            188개  sequence 1354~1541
누락            0개
첫 프레임 키프레임  188/188
조각 길이        996~1007ms  평균 1002ms
1000ms에서 100ms 초과 이탈  0개
```

`sequence`에 구멍이 없으므로 조각 누락이 0입니다. non-leaky 큐가 프레임을 버리지
않아도 디스크 쓰기 지연이 조각을 잃을 수 있는데, microSD에서 180초 동안 그런 일이
없었습니다.

### 중복 녹화 방지 (S15P11A301-142)

결함을 재현했던 순서를 그대로 밟았습니다. `no_response_timeout_seconds`를 30으로
되돌려 원래 조건을 만들었습니다.

```text
 2s CONFIRMED   BUFFERING->RECORDING (encounter=4f6a6336)
 8s LOST        RECORDING->POST_RECORDING → FINALIZING
                이벤트 저장 완료: event.mp4 4.13MB 13.0초 385프레임
                REPORT_COMMITTED 발행 4f6a6336
16s CONFIRMED   무시: 4f6a6336는 이미 마감했다
18s CONFIRMED   무시
22s CONFIRMED   무시
```

`_2` 디렉터리가 생기지 않았습니다. 가드 전에는 같은 순서에서 `_2`가 생기고 업로드
하나가 500으로 영구 실패했습니다.

가드가 과하지 않은지도 확인했습니다. 다른 `encounterId`는 정상 녹화됩니다.

```text
4f6a6336   4.13MB  mediaId=c6bef0a2  audio=있음  endReason=PERSON_LOST
c56afcae   4.18MB  mediaId=744b9b8f  audio=있음  endReason=PERSON_LOST
→ object key 충돌 없음
```

### 오디오 (S15P11A301-131)

5분 이벤트(`MAX_DURATION`)로 확인했습니다. 상세 실측은
[`sentinel_streaming/README.md`](../sentinel_streaming/README.md)에 있습니다.

```text
비디오 303.585초 9030프레임    오디오 303.585초 (aac 48000Hz 1ch)
A/V 동기  전 구간 ±28ms, 누적 경향 없음
오디오 완전성 98.8%
```

보고서가 소리 없는 두 경우를 구분합니다.

```text
마이크 있음   media.audio={"codec":"aac",...}  audioDropped=false
마이크 없음   media.audio=null                 audioDropped=false
트랙 유실     media.audio=null                 audioDropped=true    ← 결함
```

**오디오가 없다고 이벤트를 실패시키지 않습니다.** 조각에 소리가 있었는데 MP4에
없으면 로그에 error를 남기고 `audioDropped=true`로 기록하되 이벤트는 살립니다.
소리가 빠진 영상도 재생되고 사람이 찍혀 있으므로, 5분 영상을 통째로 버리는 것이
소리를 잃는 것보다 나쁩니다.

티켓은 "재생 검사가 오디오도 확인하도록 넓힌다"고 적었는데, 확인은 하되
**실패시키지는 않는 쪽**으로 구현했습니다. 위 이유 때문입니다.

**VID-12(30분 상시 쓰기)는 38장 인수 시험으로 넘깁니다.** 구현 티켓마다 30분
시험을 반복하지 않습니다. S15P11A301-107에서 VID-02를 같은 이유로 넘겼습니다.

## 실제 탐지 노드 엔드투엔드 검증 (2026-07-30, S15P11A301-158)

트리거 도구가 아니라 **실제 탐지 노드가 만든 encounter**로 전체 체인을 검증했다.
ai/detection wrapper(`src.ros_main`) → mission_manager → 이 노드 순서다. 입력은
보행자 4명 검증 영상을 CompressedImage로 20초 발행한 뒤 중단하는 시나리오다.

```text
상태 사이클   EXPLORING → CONFIRMED(personCount 4) → PERSON_APPROACHING
             → 소실 3초 → LOST → POST_RECORDING → 3초 → REPORTING
             → report committed → EXPLORING (완전 자율 사이클 복귀)
산출물       event.mp4 24.8초 739프레임 + thumbnail + report.json, 이벤트당 1개
사전 영상    preRollSeconds 3.3 (기준 3초 이상)
종료 사유    PERSON_LOST (설계 경로)
내용 검증    6개 시점 프레임 해시 전부 다름, 조각 sequence 연속(769~793)
계약        encounter.schema.json 위반 0건
```

### 다중 탐지 발행자 주의

첫 시도에서 같은 DDS 도메인의 **다른 기기가 돌리던 person_detector**(구 임시
구현)가 `/perception/person_candidates`에 함께 발행해 검증이 오염됐다. 두 탐지
노드가 공존하면 한쪽의 빈 배열 직후 다른 쪽의 후보가 도착해 mission_manager가
사람 소실을 판정할 수 없고, 이벤트가 끝나지 않아 이 노드의 30초 무응답
타임아웃(`NO_RESPONSE_TIMEOUT`)으로 55초 MP4가 만들어졌다.

- 탐지 노드는 로봇에서 **정확히 하나**만 돌아야 한다. encounter 발행자를 하나로
  모은 것과 같은 이유가 candidates에도 적용된다.
- 팀 개발 장비가 같은 네트워크·같은 `ROS_DOMAIN_ID`(기본 0)를 쓰면 다른 기기의
  노드가 실기기 임무 체인에 흘러든다. 실기기 검증 시 `ROS_DOMAIN_ID`를 분리하거나
  개발 장비의 탐지 노드를 내려야 한다. 이 검증도 도메인 분리로 재실행해 통과했다.

## 문제 해결

### 트리거를 보냈는데 반응이 없다

로그를 봅니다. 무시한 경우 사유가 남습니다.

```text
ENDED 무시: 진행 중 이벤트가 없다 (수신=ae2d6f19). CONFIRMED가 먼저 와야 한다.
CONFIRMED 무시: 다른 이벤트가 진행 중이다 (진행=11111111, 수신=389f0b60).
```

두 번째는 정상입니다. 32-6에 따라 동시에 하나만 녹화합니다. 두 이벤트가 같은
조각을 나눠 가지면 어느 MP4에 넣을지 정할 수 없습니다.

로그가 **아무것도** 없으면 메시지가 오지 않은 것입니다. 발행자를 만든 직후에
보내면 DDS 매칭이 끝나지 않아 첫 메시지가 유실됩니다. 트리거 도구는
`get_subscription_count`로 구독자를 기다립니다. 직접 발행할 때는
`ros2 topic pub`을 쓰세요. 그쪽도 매칭을 기다립니다.

### 사전 영상이 3초 미만이다

`detectedAt`을 확인합니다. 이 노드는 그 값을 정직하게 따르므로, 신호가 늦게
오면 사전 구간도 그만큼 과거가 됩니다. 링 버퍼가 8초만 보관하므로 `detectedAt`이
8초 이상 과거면 조각이 이미 없습니다.

`ros2 topic pub`으로 시험할 때는 기동에 2~3초가 걸려 `detectedAt`이 그만큼
낡습니다. 트리거 도구는 발행 시점에 값을 만듭니다.

### 조각 누락으로 MP4 생성이 실패한다

```text
MP4 생성 실패: SEGMENTS_MISSING / 누락 sequence [...]
```

`sequence`에 구멍이 있다는 뜻입니다. non-leaky 큐가 프레임을 버리지 않아도 디스크
쓰기 지연이 조각을 잃을 수 있습니다. PoC-B 조건 6이 측정하려던 것이며 microSD의
쓰기 지연 스파이크가 원인 후보입니다.
