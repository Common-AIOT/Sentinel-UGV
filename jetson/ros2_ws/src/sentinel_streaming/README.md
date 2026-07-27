# sentinel_streaming

ROS 압축 토픽을 H.264로 인코딩해 MediaMTX에 발행합니다 (S15P11A301-106).

계약과 성능 전제는 S15P11A301-62에서 확정했습니다. 규범은 명세 32장이고 실측 기준선은 [`jetson/streaming_poc/README.md`](../../../streaming_poc/README.md)입니다.

## 파이프라인

```text
/camera/image_raw/compressed        (usb_cam이 단독 점유, 62에서 확정)
  -> appsrc -> jpegparse -> nvv4l2decoder mjpeg=1
  -> nvvidconv -> I420 -> x264enc -> h264parse
  -> tee
       +-- queue(leaky=downstream) -> MediaMTX 발행
       +-- queue(non-leaky)        -> 링 버퍼 writer (S15P11A301-123)
```

카메라를 직접 열지 않습니다. 명세 32-3의 카메라 단일 오픈 원칙에 따라 `usb_cam`만 `/dev/video*`를 열고, 이 노드는 압축 토픽을 구독합니다.

링 버퍼 분기는 `enable_record_branch`가 `false`인 동안 `fakesink`로 종단됩니다. tee 지점만 유지하고 소비는 S15P11A301-123이 담당합니다.

## 실행

```bash
# 카메라·라이다 (다른 터미널)
ros2 launch sentinel_bringup sensors.launch.py

# 스트리밍 경로
ros2 launch sentinel_streaming streaming.launch.py
```

## 발행 모드

`rtspclientsink`가 표준 경로지만 `gstreamer1.0-rtsp` 패키지가 필요합니다.

| 모드 | 구성 | 추가 패키지 |
|---|---|---|
| `rtsp` (기본) | `rtspclientsink` → MediaMTX `source: publisher` | `gstreamer1.0-rtsp` |
| `udp_mpegts` | `mpegtsmux` + `udpsink` → MediaMTX `udp+mpegts://` | 없음 |

```bash
ros2 launch sentinel_streaming streaming.launch.py publish_mode:=udp_mpegts
```

`rtsp`를 지정했는데 `rtspclientsink`가 없으면 노드가 경고를 남기고 `udp_mpegts`로 폴백합니다. `udp_mpegts`를 launch 인자로 주면 `MTX_PATHS_SENTINEL_SOURCE` 환경변수로 MediaMTX 경로 source까지 자동 주입되므로 `mediamtx.yml`을 고치지 않아도 됩니다.

`udp_mpegts`는 MPEG-TS 먹싱이 한 단계 늘어납니다. 기본은 `rtsp`를 쓰는 것이 맞습니다.

## PTS 규칙

62 계약을 그대로 구현합니다.

```text
첫 프레임 PTS = 0
이후 PTS      = 현재 header.stamp - 첫 header.stamp
```

`header.stamp`가 뒤로 가면(카메라 재시작, 시각 점프) PTS를 리베이스하고 `~/segment_boundary` 토픽으로 경계를 알립니다. 링 writer는 이 신호를 받아 진행 중인 조각을 즉시 마감해야 합니다. 시간 불연속을 조각 경계로만 겪게 해야 재생 호환성 문제를 막습니다.

## 장애 복구

입력(카메라)과 출력(MediaMTX) 장애를 **따로** 셉니다. MediaMTX 재시작은 카메라 결함이 아니므로 같은 카운터를 쓰면 `CAMERA_FAULT`를 오판합니다.

| 장애 | 감지 | 동작 |
|---|---|---|
| 입력 끊김 | 프레임 3초 미수신 | 1·2·4초 백오프 3회 재시작 → `CAMERA_FAULT` 후 정지 |
| 출력 장애 | GStreamer 버스 오류 | 1·2·4초 백오프 재구성 (한도 없음) |

`CAMERA_FAULT`는 `~/status` 토픽으로, PTS 리베이스와 재구성은 `~/segment_boundary` 토픽으로 나갑니다. 두 신호를 한 토픽에 섞으면 링 writer가 결함을 경계로 오해합니다.

자율 탐사를 `PAUSED`로 전환하는 것은 Mission Manager 책임이고 이 노드는 상태만 알립니다.

### GStreamer 버스는 폴링해야 합니다

`bus.add_signal_watch()`는 GLib 메인루프가 돌아야 메시지를 dispatch합니다. `rclpy.spin()`은 ROS 실행자만 돌리므로 **시그널 콜백이 영원히 호출되지 않습니다.** 같은 이유로 `GLib.timeout_add_seconds`도 발화하지 않습니다.

그래서 ROS 타이머(0.2초)에서 `bus.pop_filtered`로 직접 폴링하고, 재시작 타이머도 ROS 일회성 타이머를 씁니다. 이 구조를 바꿀 때는 MediaMTX를 `kill -9`한 뒤 스트림이 복구되는지 반드시 확인하세요.

## HTTPS

EC2에서 HTTPS로 열린 관제 페이지가 Jetson의 평문 HTTP 신호 주소에 접근하면 브라우저가 혼합 콘텐츠로 차단합니다(32-4). LAN 환경이라 공인 인증서를 받을 수 없으므로 자체 서명 인증서를 씁니다.

```bash
./scripts/gen_stream_cert.sh
ros2 launch sentinel_streaming streaming.launch.py webrtc_encryption:=true
```

인증서 경로는 `mediamtx.yml`에 박지 않고 환경변수(`MTX_WEBRTCSERVERCERT` 등)로 주입합니다. 인증서는 커밋 대상이 아니고 배포마다 다릅니다. `.gitignore`가 `*.crt`·`*.key`를 제외합니다.

**관제 노트북에 인증서를 신뢰 등록하는 것은 사람이 해야 합니다.** 등록하지 않으면 브라우저가 WHEP 요청을 차단합니다.

## 주의: `gi`는 시스템 파이썬에만 있습니다

`PyGObject`(`python3-gi`)는 apt로 설치되어 시스템 파이썬에 있습니다. 프로젝트 `.venv`는 `include-system-site-packages = false`라서 venv를 활성화한 상태로 노드를 실행하면 `ModuleNotFoundError: No module named 'gi'`가 납니다.

`ros2 launch`와 `ros2 run`은 시스템 파이썬을 쓰므로 정상 동작합니다. 수동 실행 시에는 venv를 비활성화하세요.

## 2026-07-27 검증

표준 `rtsp` 모드(`gstreamer1.0-rtsp` 설치 후), 카메라·라이다 동시 구동.

```text
파이프라인      : PLAYING, 첫 프레임 수신 후 PTS 기준 0 설정
MediaMTX        : [RTSP] session is publishing to path 'sentinel'
RTSP 되읽기     : h264, Constrained Baseline, 1280x720,
                  has_b_frames=0, avg_frame_rate=30/1
WHEP (HTTP)     : HTTP 204
WHEP (HTTPS)    : HTTP 204, 평문 접근은 400으로 거부
30초 처리량     : 906 패킷 / 30.0초 = 30.2 fps, 약 2.43 Mbps
```

| 검증 항목 | 결과 |
|---|---|
| PoC-B 실측 재현 | 합격. H.264 속성과 처리량 동일 |
| VID-08 AI 지연 주입 격리 | 합격. YOLO 15 FPS 부하 중 25초 되읽기 756패킷 = 30.24 fps, `CAMERA_FAULT` 0, PTS 리베이스 0 |
| MediaMTX 강제 종료 격리 | 합격. 노드 PID 유지, 카메라 29.5 Hz 유지 |
| MediaMTX 재시작 후 복구 | 합격. 재구성 2회로 스트림 자동 복구, `CAMERA_FAULT` 오판 0 |
| 입력 끊김 시 `CAMERA_FAULT` | 합격. 1/3·2/3·3/3 백오프(1·2·4초) 후 선언, 이후 재시작 없음 |
| HTTPS WHEP | 합격. `:8889 (TCP/HTTPS)`, 자체 서명 인증서 |
| **VID-02** 30분 연속 스트리밍 | 합격. 아래 참조 |

VID-02 상세 (HTTPS 모드, 카메라·라이다 동시, 1801.6초 연속 되읽기):

```text
패킷        : 54,007개 / 1801.6초 = 29.977 fps
최대 간격   : 0.033초 (정상 프레임 주기와 동일, 3초 초과 0회)
프로세스    : 30초 간격 60회 샘플 전 구간 mediamtx·node·usb_cam 생존
메모리      : 최대 3362MB / 7619MB
온도        : tj 최대 46.1°C
ffmpeg 오류 : 0
```

`CAMERA_FAULT` 검증은 USB를 물리적으로 뽑는 대신 `usb_cam` 노드를 강제 종료해 토픽을 끊는 방식으로 했습니다. 노드 관점에서는 같은 경로(입력 미수신)입니다.

## 아직 검증되지 않은 것

- **VID-01** 브라우저 WebRTC 지연 30회 측정. 엔드포인트는 동작하지만 측정 주체가 브라우저 클라이언트이므로 S15P11A301-107과 함께 봅니다.
- 관제 노트북의 인증서 신뢰 등록. 사람이 해야 합니다.
- USB 케이블을 실제로 분리했을 때의 동작. 위 대체 검증으로 코드 경로는 확인했지만 USB 재연결 시 장치 번호가 바뀌는 경우는 다릅니다.

## 문제 해결

### 영상이 간헐적으로 끊겼다 붙었다 한다

**가장 흔한 원인은 `stream_pipeline` 노드가 두 개 도는 것입니다.** MediaMTX는 한 경로에 발행자 하나만 허용하므로, 두 노드가 `sentinel` 경로를 서로 빼앗으면서 각자 쫓겨나고 재구성을 반복합니다.

증상이 네트워크 문제처럼 보여서 원인을 찾기 어렵습니다. 로그에 이 조합이 보이면 이 경우입니다.

```text
GStreamer 오류 [rtsp]: Could not write to resource.
출력 경로 장애(rtsp). N초 후 파이프라인을 다시 세운다 (누적 20회)
```

`ros2 launch`를 Ctrl+C나 kill로 끊었을 때 자식 노드가 살아남으면 발생합니다. 확인과 정리는 이렇게 합니다.

```bash
pgrep -af "lib/sentinel_streaming/stream_pipeline"
# 두 개 이상이면 전부 정리한 뒤 다시 launch 한다
pkill -f "lib/sentinel_streaming/stream_pipeline"
```

MediaMTX 로그에서 발행 세션이 계속 새로 생기는 것으로도 확인할 수 있습니다.

```bash
grep -c "is publishing to path" <launch 로그>
```

정상이면 실행당 1~2회입니다. 수십 회면 발행 경쟁입니다.

### 브라우저에서 영상이 안 나오고 상태가 변하지 않는다

`.env.local`의 주소가 **VS Code 포트 포워딩(`127.0.0.1`)을 경유하면** 터널이 끊길 때 브라우저의 WHEP POST가 Jetson에 도달하지 않습니다. 이때 MediaMTX 로그에는 아무 기록도 남지 않으므로, 로그가 비어 있으면 이 경우를 의심하세요.

Jetson의 실제 IP를 직접 쓰는 것이 맞습니다. 지연 측정도 터널을 거치면 오염됩니다.

```bash
echo 'NEXT_PUBLIC_LOCAL_STREAM_URL=http://<젯슨IP>:8889/sentinel/whep' > frontend/.env.local
```

`.env.local` 변경은 dev 서버 재시작이 필요합니다.

### MediaMTX 내장 플레이어로 먼저 확인한다

앱을 거치지 않고 스트림 자체를 볼 수 있습니다. 여기서 안 나오면 서버 문제, 나오는데 앱에서 안 나오면 앱 문제로 범위가 좁혀집니다.

```text
http://<젯슨IP>:8889/sentinel        플레이어 페이지
http://<젯슨IP>:8889/sentinel/whep   POST 전용 API (브라우저로 열면 오류)
rtsp://127.0.0.1:8554/sentinel       RTSP. 브라우저로 열 수 없다
```

## 알려진 사항

여러 차례 `kill -9`로 프로세스를 정리한 뒤에는 `/dev/shm`에 잔여 파일이 남아 Fast DDS SHM 오류(`open_and_lock_file failed`)가 나타날 수 있습니다. PoC-B에서 이 오류가 데이터 전달을 막지 않음을 확인했으나, 깨끗한 상태로 측정하려면 `rm -f /dev/shm/fastrtps_*`로 정리한 뒤 시작하세요.

## 명세와 다른 점

명세 32-7은 이 기능을 `jetson/media/camera_pipeline/`, `jetson/media/streaming/` 아래의 독립 모듈로 정의합니다. 그 구조는 GStreamer가 카메라를 직접 여는 설계를 전제했습니다.

62에서 카메라 단일 오픈이 `usb_cam` + ROS 압축 토픽으로 확정되면서 이 노드는 ROS 토픽 구독자가 되었고, 따라서 ROS 2 워크스페이스 패키지로 두는 것이 맞습니다. 실행·파라미터·의존성 관리를 ROS 도구가 처리하고 `sensors.launch.py`와 같은 방식으로 다룰 수 있습니다.

명세 32-7의 디렉터리 구조 정정은 별도로 처리해야 합니다.
