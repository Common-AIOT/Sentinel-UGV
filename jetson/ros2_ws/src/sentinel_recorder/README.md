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

## 오디오는 없습니다

32-5는 "H.264/AAC 재다중화"라고 적었지만 `usb_cam`이 오디오를 발행하지 않고
파이프라인에 오디오 경로가 없습니다. 지금은 비디오만 넣습니다. 오디오는 음성
상호작용 티켓과 연계해 정합니다(32-6).

## pending 상한

TBD-VID-001에서 정한 **30분 분량(약 560MB)**입니다. 실측 상한이 562MB로
확정값과 맞습니다.

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

상한을 25초(약 7.8MB)로 줄여 3단계 전부를 타게 했습니다. 실제 상한 562MB에서
이벤트 180여 건을 녹화하는 것과 같은 경로입니다.

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
— 이벤트를 잃는 것이 아니라 이미 업로드된 것을 조금 일찍 지웁니다. 562MB 상한에서
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

**VID-12(30분 상시 쓰기)는 38장 인수 시험으로 넘깁니다.** 구현 티켓마다 30분
시험을 반복하지 않습니다. S15P11A301-107에서 VID-02를 같은 이유로 넘겼습니다.

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
