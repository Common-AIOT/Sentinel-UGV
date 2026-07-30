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
       +-- queue(non-leaky)        -> splitmuxsink (링 버퍼, S15P11A301-123)
                                        ^
pulsesrc -> queue(non-leaky) -> audioconvert -> audioresample
         -> voaacenc -> aacparse ------------- audio_0 pad (S15P11A301-131)
```

카메라를 직접 열지 않습니다. 명세 32-3의 카메라 단일 오픈 원칙에 따라 `usb_cam`만 `/dev/video*`를 열고, 이 노드는 압축 토픽을 구독합니다.

링 버퍼 분기는 `enable_record_branch`가 `false`인 동안 `fakesink`로 종단됩니다. tee 지점만 유지하고 소비는 S15P11A301-123이 담당합니다.

## 오디오 (S15P11A301-131)

32-5가 "H.264/AAC 재다중화"를 정했고 32-6이 요구조자와의 대화를 증빙으로 둡니다. 로봇이 묻고 사람이 답하는 대화가 통째로 들리는 것이 구조화 보고서만 남기는 것보다 완전합니다.

오디오는 **링 분기에만** 붙습니다. 관제 스트리밍(RTSP)에는 넣지 않습니다. 남겨야 하는 것은 이벤트 파일이고, 실시간 화면에 소리를 얹으면 지연 예산(32-8)이 늘어납니다.

소리는 ROS 토픽을 거치지 않습니다. `usb_cam`이 오디오를 발행하지 않으므로 GStreamer 안에서 `pulsesrc`로 직접 들어옵니다. BRIO 100은 PulseAudio에만 잡힙니다.

```text
alsa_input.usb-046d_Brio_100_...-02.mono-fallback   s16le 1ch 48000Hz
```

### 두 개의 클럭

이 브랜치의 유일한 실질 위험입니다.

```text
비디오   appsrc do-timestamp=false, PTS = usb_cam stamp - 첫 stamp
오디오   pulsesrc, 파이프라인 클럭
```

둘 다 CLOCK_MONOTONIC 기반이지만 기준이 다르므로 긴 이벤트에서 드리프트가 쌓일 수 있습니다. 5분(`max_event_seconds`) 실측을 아래에 남깁니다.

### 큐는 leaky가 아닙니다

비디오 녹화 분기와 같은 정책입니다. 오디오를 버리면 그 구간이 무음이 되고, 대화 증빙에서 빠진 구간은 복구할 수 없습니다.

큐 자체는 있어야 합니다. 오디오가 막히면 `mpegtsmux`가 비디오도 기다립니다.

### 마이크가 없으면 오디오만 끕니다

`pulsesrc`가 PLAYING 전환에서 실패하면 노드가 오디오를 끄고 비디오만으로 파이프라인을 다시 세웁니다.

이 분기가 없으면 마이크가 없는 기기에서 재구성이 무한 반복되고 **녹화가 영구히 멈춥니다.** 소리 때문에 영상을 잃는 것이 더 나쁩니다.

판정은 GStreamer 오류의 소스 요소 이름으로 합니다(`ring_buffer.is_audio_element`). 실제 실패에서 확인한 값입니다.

```text
ERROR: from element /GstPipeline:pipeline0/GstPulseSrc:pulsesrc0:
       Failed to connect stream: Invalid argument
```

한 번 끄면 다시 켜지지 않습니다. `RingBufferWriter`는 `__init__`에서 한 번만 만들어지고 재구성은 `sink_description()`만 다시 부르므로, 끈 값이 재구성 뒤에도 남습니다.

### 마이크를 바꿀 때

마이크는 확정되지 않았습니다(TBD-AUD-001). BRIO 100 내장 마이크가 잠정이고 주행 소음 환경에서 STT 인식률이 미달하면 USB 마이크로 바꿉니다. 그때 고치는 것은 `media.yaml` 한 줄입니다.

```yaml
audio_source: alsasrc device=hw:2
```

`audioconvert`와 `audioresample`을 앞에 두므로 마이크의 원래 형식이 달라도 caps로 맞춰집니다.

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

## HTTPS — 공인 인증서 (S15P11A301-145)

EC2에서 HTTPS로 열린 관제 페이지가 Jetson의 평문 HTTP 신호 주소에 접근하면 브라우저가 혼합 콘텐츠로 차단합니다(32-4). 그래서 WHEP 엔드포인트도 HTTPS여야 합니다.

`jetson.sentinel-ugv.xyz`의 Let's Encrypt 인증서를 씁니다. 공인 인증서라 **어느 기기에서도 신뢰 등록 없이** 접속됩니다. 자체 서명(아래 폴백)은 보는 노트북마다 등록이 필요해 "그 노트북에서만 되는 데모"가 됩니다.

```text
DNS      jetson.sentinel-ugv.xyz  A  70.12.247.77  (가비아, TTL 600)
인증서    /etc/letsencrypt/live/jetson.sentinel-ugv.xyz/   (root 전용)
배치      ~/.config/sentinel/certs/server.crt / server.key (orin이 읽는 사본)
만료      2026-10-27
```

```bash
ros2 launch sentinel_streaming streaming.launch.py webrtc_encryption:=true
```

인증서 경로는 `mediamtx.yml`에 박지 않고 환경변수(`MTX_WEBRTCSERVERCERT` 등)로 주입합니다. 인증서는 커밋 대상이 아닙니다. `.gitignore`가 `*.crt`·`*.key`를 제외합니다.

관제 웹은 이 값 하나만 바꾸면 됩니다.

```text
NEXT_PUBLIC_LOCAL_STREAM_URL=https://jetson.sentinel-ugv.xyz:8889/sentinel/whep
```

### 왜 DNS-01인가

젯슨이 NAT 뒤에 있습니다(로컬 70.12.247.77, 인터넷에서 본 IP는 다름). HTTP-01은 Let's Encrypt가 외부에서 접속해야 해서 안 되고, DNS-01은 TXT 레코드로 소유를 증명하므로 인바운드가 필요 없습니다.

같은 이유로 이 주소는 **SSAFY 네트워크 안에서만** 통합니다. A 레코드가 내부 IP를 가리키므로 밖에서는 연결되지 않습니다. 명세 32-4가 시연 기본 경로를 LAN 직접 연결로 정했으므로 의도와 일치합니다. 원격 시청(EC2 중계)은 선택 기능이며 기능 축소 1순위입니다.

### 갱신 (90일마다, 수동)

`--manual` 발급이라 **자동 갱신이 없습니다.** 만료 30일 전에 janjonghwa@gmail.com으로 알림이 옵니다. 절차:

```bash
# 1. certbot을 실행하고 TXT 값이 출력되면 멈춘 상태로 둔다.
#    ★ Enter를 먼저 누르지 않는다. 토큰은 실행마다 새로 나온다.
sudo certbot certonly --manual --preferred-challenges dns \
  -d jetson.sentinel-ugv.xyz -m janjonghwa@gmail.com --agree-tos

# 2. 가비아 콘솔 → DNS 관리툴에서 TXT 수정
#    호스트: _acme-challenge.jetson   값: <출력된 토큰>
#    (호스트 칸에 도메인을 붙이지 않는다. 가비아가 자동으로 붙인다)

# 3. 다른 터미널에서 반영 확인 후 Enter
host -t TXT _acme-challenge.jetson.sentinel-ugv.xyz
#    권한 서버 직접 확인: host -t TXT _acme-challenge.jetson.sentinel-ugv.xyz ns.gabia.net

# 4. 발급되면 orin이 읽을 수 있게 다시 배치
sudo cp /etc/letsencrypt/live/jetson.sentinel-ugv.xyz/fullchain.pem ~/.config/sentinel/certs/server.crt
sudo cp /etc/letsencrypt/live/jetson.sentinel-ugv.xyz/privkey.pem  ~/.config/sentinel/certs/server.key
sudo chown orin:orin ~/.config/sentinel/certs/server.{crt,key}
chmod 600 ~/.config/sentinel/certs/server.key

# 5. MediaMTX 재시작
```

**순서가 중요합니다.** 첫 발급 때 TXT를 넣기 전에 Enter를 눌러 실패했고, 그 옛 값이 캐시(TTL 600초)에 박혀 다음 시도까지 막았습니다. certbot을 멈춰 둔 채 레코드를 넣으면 이 문제가 없습니다.

### 폴백 — 자체 서명

인터넷이 없는 환경에서는 `./scripts/gen_stream_cert.sh`로 자체 서명 인증서를 만듭니다. 그 경우 **보는 노트북마다 인증서를 신뢰 등록해야 하고, 그것은 사람이 해야 합니다.** 기존 자체 서명은 `~/.config/sentinel/certs/selfsigned.{crt,key}`로 남겨 두었습니다.

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

## 2026-07-29 검증 — 오디오 (S15P11A301-131)

카메라·라이다·YOLO 탐지를 동시에 구동한 실제 경로입니다.

| 검증 항목 | 결과 |
|---|---|
| 조각에 두 스트림이 들어가는가 | 합격. `h264,video` + `aac,audio 48000Hz 1ch` |
| 이벤트 MP4에 두 스트림이 남는가 | 합격. 5분 이벤트에서 비디오 303.585초, 오디오 303.585초 |
| 5분 A/V 동기 | 합격. 전 구간 ±28ms 이내, 누적 경향 없음 |
| 오디오 완전성 | 합격(수정 후). 98.8% |
| 마이크 없을 때 폴백 | 합격. 오디오만 끄고 비디오 조각 계속 생성 |
| PoC-B 조건 6 (조각 누락 0) | 합격. 304조각 sequence 연속, 누락 0 |

### 오디오가 38% 사라졌던 결함

처음 5분 측정에서 1초 조각마다 오디오가 **620ms만** 담겼습니다. 로그에
`Can't record audio fast enough`가 반복됐습니다.

```text
조각별 실측 (수정 전)
  seg_000000  비디오 30f=1000.0ms   오디오 32f= 682.7ms
  seg_000003  비디오 30f=1000.0ms   오디오 29f= 618.7ms
  seg_000006  비디오 30f=1000.0ms   오디오 28f= 597.3ms
  합계        비디오 7.867초        오디오 4.864초   61.8%
```

부하를 낮추면 재현되지 않는다는 점이 원인을 가리켰습니다.

```text
videotestsrc만 (부하 낮음)          오디오/비디오 99.5%
실제 경로 + YOLO 탐지 동시 구동     오디오/비디오 61.8%
```

구조 문제가 아니라 스케줄링 문제입니다. `x264enc`(CPU 인코딩, 74%)와 YOLO
탐지(52%)가 코어를 다 쓰면 `pulsesrc`의 읽기 스레드가 늦게 깨고, 그동안
PulseAudio 링버퍼가 넘쳐 샘플이 버려집니다. **손실은 큐에 닿기 전 소스 안에서
일어나므로 큐를 키워도 소용없습니다.**

`buffer-time`을 200ms에서 2초로, `latency-time`을 10ms에서 50ms로 올려
고쳤습니다.

```text
수정 후 (같은 부하)
  조각 8개 전부  비디오 30f=1000.0ms   오디오 47f=1002.7ms
  합계           비디오 8.000초        오디오 8.000초   100.0%
  pulsesrc 경고  0건
```

5분 이벤트에서도 유지됩니다.

```text
                        수정 전        수정 후
오디오 완전성            61.8%          98.8%
비디오/오디오 길이 차이   200.0ms        0.0ms
MP4 길이                314.7초        303.6초   (조각 304개 = 약 304초)
pulsesrc 경고            14건           0건
```

수정 전 MP4가 10초 길었던 것은 오디오 구멍이 조각 이음마다 타임라인을 늘렸기
때문입니다. 남은 1.2%(3.09초)는 조각 이음 166곳의 평균 18.6ms로, AAC 프레임
길이(21.33ms)보다 작은 반올림 몫입니다.

### 5분 A/V 동기 실측

```text
경과      비디오PTS   오디오PTS       A-V
  30s      29.999      29.984      -14.7ms
  60s      59.979      59.998      +18.7ms
 120s     119.967     119.991      +24.0ms
 180s     179.976     179.999      +22.7ms
 240s     239.991     239.999       +8.0ms
 300s     299.999     299.994       -5.4ms
```

부호가 오가고 크기가 커지지 않으므로 **누적 드리프트가 없습니다.** 두 클럭이
서로 다른 기준을 쓰지만 둘 다 CLOCK_MONOTONIC에 묶여 있어서 장기 편차가 생기지
않습니다.

이 측정에는 박수 같은 신호가 필요하지 않습니다. 비디오와 오디오의 PTS를 직접
비교하므로 사람이 지켜볼 필요가 없고, 입술 동기를 눈으로 판정하는 것보다
정확합니다.

### fps

```text
비디오 9030프레임 / 303.551초 = 29.748 fps
```

PoC-B 기준선 30.020 fps보다 0.9% 낮습니다. 이 측정 중 22초 동안 마이크 폴백
시험용 노드를 같이 띄워 `x264enc`가 두 개 돌았습니다. 그만큼은 이 시험의
부하이지 오디오 때문이 아닙니다. 오디오 인코딩(`voaacenc` 64kbps 1채널)은 CPU를
거의 쓰지 않습니다.

### 마이크가 없을 때

존재하지 않는 PulseAudio 장치를 주어 확인했습니다.

```text
[ERROR] GStreamer 오류 [pulsesrc0]: Failed to connect stream: Invalid argument
[WARN]  오디오 없이 다시 세운다. 이벤트 영상에 소리가 담기지 않지만
        녹화와 스트리밍은 유지된다.
조각 8개 생성, 스트림 h264,video 단독
보고서 media.audio=null  audioDropped=false
```

마이크가 있을 때와 비교하면 두 경우가 구분됩니다.

```text
마이크 있음   media.audio={"codec":"aac",...}  audioDropped=false
마이크 없음   media.audio=null                 audioDropped=false
트랙 유실     media.audio=null                 audioDropped=true    ← 결함
```

## 2026-07-29 검증 — 공인 인증서 WHEP (S15P11A301-145)

| 검증 항목 | 결과 |
|---|---|
| 도메인 TLS 핸드셰이크 | 합격. `curl` 인증서 검증 켠 상태(-k 없음)로 OPTIONS 204 |
| 체인 검증 | 합격. 시스템 CA 번들로 `Verify return code: 0 (ok)` |
| 제공 인증서 | `CN=jetson.sentinel-ugv.xyz`, issuer Let's Encrypt, 만료 2026-10-27 |
| 평문 거부 | 합격. `http://` 시도 400 |
| 스트림 재발행 | 합격. TLS 재기동 후 rtspclientsink가 자동 재연결, 1 track (H264) |
| MoQ 비활성 | `moq: no` 적용. 8892가 더는 열리지 않음 |

브라우저 확인 주소: `https://jetson.sentinel-ugv.xyz:8889/sentinel/` (인증서 경고 없이 열려야 정상)

## 아직 검증되지 않은 것

- **오디오 내용 자체.** 두 스트림이 있고 길이가 맞는 것은 확인했지만, 실제로 무슨 소리가 담겼는지는 듣지 않았습니다. 음성 상호작용 티켓에서 STT가 이 오디오를 쓰게 되면 그때 인식률로 판정됩니다(TBD-AUD-001).
- **VID-01** 브라우저 WebRTC 지연 30회 측정. HTTPS가 열려 이제 관제 웹에서 측정할 수 있게 됐습니다(S15P11A301-107의 `useWhepStream`이 지연 추정을 내장). 관제 웹에 `NEXT_PUBLIC_LOCAL_STREAM_URL`이 반영된 뒤 측정합니다.
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
