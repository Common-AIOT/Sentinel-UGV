#!/usr/bin/env python3
"""ROS 압축 토픽을 H.264로 인코딩해 MediaMTX에 발행한다 (S15P11A301-106).

파이프라인 (S15P11A301-62 확정 계약):

    /camera/image_raw/compressed
      -> appsrc -> jpegparse -> nvv4l2decoder mjpeg=1
      -> nvvidconv -> I420 -> x264enc -> h264parse
      -> tee
           +-- queue(leaky)     -> rtspclientsink -> MediaMTX
           +-- queue(non-leaky) -> 링 버퍼 writer (S15P11A301-123)

카메라를 직접 열지 않는다. `usb_cam`이 단독 점유하며 이 노드는 압축 토픽만
구독한다. 명세 32-3의 카메라 단일 오픈 원칙이다.

주의: `gi`(PyGObject)는 시스템 파이썬에만 있다. 프로젝트 `.venv`를 활성화한
상태로 실행하면 임포트가 실패한다. `ros2 launch`는 시스템 파이썬을 쓰므로
정상 동작한다.
"""

import gi

gi.require_version('Gst', '1.0')
gi.require_version('GstApp', '1.0')

from gi.repository import Gst  # noqa: E402

import rclpy  # noqa: E402
from rclpy.node import Node  # noqa: E402
from rclpy.qos import (  # noqa: E402
    DurabilityPolicy,
    HistoryPolicy,
    QoSProfile,
    ReliabilityPolicy,
)
from sensor_msgs.msg import CompressedImage  # noqa: E402
from std_msgs.msg import String  # noqa: E402

from .ring_buffer import RingBufferWriter, is_audio_element  # noqa: E402


class StreamPipelineNode(Node):
    """압축 토픽을 구독해 GStreamer appsrc로 밀어넣고 H.264로 인코딩한다."""

    def __init__(self) -> None:
        super().__init__('stream_pipeline')
        Gst.init(None)

        self._declare_parameters()

        self.pipeline: Gst.Pipeline | None = None
        self.appsrc: Gst.Element | None = None
        self.publish_mode_effective = 'rtsp'

        # PTS 기준. 첫 프레임 stamp를 0으로 잡고 이후는 차분을 쓴다(62 계약).
        self.first_stamp_ns: int | None = None
        self.last_stamp_ns: int | None = None
        self.frames_pushed = 0

        # 한 번이라도 프레임을 받았는지. 입력 감시 가드에 쓴다.
        # first_stamp_ns는 재구성 때마다 None으로 돌아가므로 가드로 쓸 수 없다.
        # 그걸 가드로 쓰면 1회 재시작 후 감시가 영구히 멈춰 CAMERA_FAULT에
        # 도달하지 못한다.
        self.stream_started = False

        # 재시작 상태. 무한 재시작을 막기 위해 시도 횟수를 센다.
        self.restart_attempts = 0
        self.fault_declared = False

        # 세그먼트 경계 신호. PTS 리베이스가 일어나면 발행한다.
        # 소비하는 쪽(링 writer)은 S15P11A301-123이다.
        self.boundary_pub = self.create_publisher(
            String, '~/segment_boundary', 10)

        # 노드 상태. CAMERA_FAULT를 세그먼트 경계와 섞으면 링 writer가
        # 결함을 경계 신호로 오해한다. 별도 토픽으로 낸다.
        self.status_pub = self.create_publisher(String, '~/status', 10)

        # 출력 sink 장애는 카메라 결함과 구분해서 센다. MediaMTX 재시작은
        # CAMERA_FAULT가 아니므로 같은 카운터를 쓰면 오판한다.
        self.sink_restart_attempts = 0
        self.pending_restart = False
        self.restart_timer = None

        # 링 버퍼 (S15P11A301-123, 32-5). 분기가 꺼져 있으면 만들지 않는다.
        #
        # 디렉터리를 만들 수 없으면 링 버퍼 없이 계속한다. 관제 스트리밍이 녹화
        # 준비 실패로 멈추면 안 된다(32장 장애 격리). 녹화 노드는 index.json이
        # 없는 것으로 상황을 안다.
        self.ring: RingBufferWriter | None = None
        if self._param('enable_record_branch'):
            candidate = RingBufferWriter(
                self._param('buffer_directory'),
                int(self._param('segment_seconds')),
                int(self._param('ring_segments')),
                send_keyframe_requests=bool(
                    self._param('send_keyframe_requests')
                ),
                audio_enabled=bool(self._param('enable_audio')),
                audio_source=str(self._param('audio_source')),
                audio_encoder=str(self._param('audio_encoder')),
                audio_rate=int(self._param('audio_rate')),
                audio_channels=int(self._param('audio_channels')),
                audio_queue_seconds=int(self._param('audio_queue_seconds')),
                logger=self.get_logger(),
            )
            try:
                candidate.prepare()
                self.ring = candidate
                self.get_logger().info(
                    f'링 버퍼: {candidate.directory} '
                    f'{candidate.segment_seconds}초 조각 × {candidate.ring_segments}개'
                )
            except OSError as error:
                self.get_logger().error(
                    f'링 버퍼 디렉터리를 만들 수 없다({error}). '
                    '녹화 없이 스트리밍만 계속한다.'
                )

        self._build_pipeline()
        self._create_input_subscription()

        # 입력 감시 타이머. 프레임이 끊기면 재시작 백오프를 돈다.
        self.create_timer(1.0, self._check_input_alive)
        # 입력과 독립된 링 writer 감시. 프로세스가 살아 있는 교착을 잡는다.
        self.create_timer(1.0, self._check_ring_alive)
        # GStreamer 버스 폴링. 시그널 워치는 GLib 루프가 없어 동작하지 않는다.
        self.create_timer(0.2, self._poll_bus)
        self.last_frame_wall = self.get_clock().now()

    # ------------------------------------------------------------------
    # 파라미터
    # ------------------------------------------------------------------
    def _declare_parameters(self) -> None:
        self.declare_parameter('input_topic', '/camera/image_raw/compressed')
        self.declare_parameter('input_width', 1280)
        self.declare_parameter('input_height', 720)
        self.declare_parameter('input_framerate', 30)
        self.declare_parameter('decoder', 'nvv4l2decoder')
        self.declare_parameter('decoder_fallback', 'jpegdec')
        self.declare_parameter('encoder_bitrate_kbps', 2500)
        self.declare_parameter('encoder_speed_preset', 'ultrafast')
        self.declare_parameter('encoder_tune', 'zerolatency')
        self.declare_parameter('encoder_key_int_max', 30)
        self.declare_parameter('encoder_bframes', 0)
        self.declare_parameter('publish_mode', 'rtsp')
        self.declare_parameter('rtsp_url', 'rtsp://127.0.0.1:8554/sentinel')
        self.declare_parameter('udp_host', '127.0.0.1')
        self.declare_parameter('udp_port', 8890)
        self.declare_parameter('stream_queue_buffers', 3)
        self.declare_parameter('record_queue_buffers', 60)
        self.declare_parameter('enable_record_branch', False)
        # 링 버퍼 (S15P11A301-123, 명세 32-5)
        self.declare_parameter(
            'buffer_directory', '/var/lib/sentinel/media/buffer'
        )
        self.declare_parameter('segment_seconds', 1)
        self.declare_parameter('ring_segments', 8)
        # 카메라 입력은 살아 있는데 이 시간 동안 새 조각이 열리지 않으면
        # splitmuxsink 교착으로 보고 파이프라인을 재구성한다(S15P11A301-161).
        self.declare_parameter('ring_stall_timeout_seconds', 3.0)
        # true면 splitmuxsink가 상류에 force-keyframe을 보내 조각이 한 번 더
        # 쪼개진다. 실측에서 1001ms와 30ms가 번갈아 나왔다. ring_buffer.py 주석 참고.
        self.declare_parameter('send_keyframe_requests', False)
        # 이벤트 영상 오디오 (S15P11A301-131, 명세 32-5·32-6).
        #
        # 소스와 인코더를 설정값으로 둔다. 마이크가 확정되지 않았다 —
        # BRIO 100 내장이 잠정이고 STT 인식률 미달 시 USB 마이크로 바꾼다
        # (TBD-AUD-001).
        self.declare_parameter('enable_audio', False)
        self.declare_parameter('audio_source', 'pulsesrc')
        self.declare_parameter('audio_encoder', 'voaacenc bitrate=64000')
        self.declare_parameter('audio_rate', 48000)
        self.declare_parameter('audio_channels', 1)
        self.declare_parameter('audio_queue_seconds', 3)
        self.declare_parameter('restart_backoff_seconds', [1.0, 2.0, 4.0])
        self.declare_parameter('restart_max_attempts', 3)
        self.declare_parameter('input_timeout_seconds', 3.0)

    def _param(self, name: str):
        return self.get_parameter(name).value

    # ------------------------------------------------------------------
    # 파이프라인 구성
    # ------------------------------------------------------------------
    def _decoder_description(self) -> str:
        """디코더 요소를 고른다. nvv4l2decoder가 없으면 SW 폴백을 쓴다.

        PoC-A에서 nvjpegdec는 약 196ms/frame으로 기각됐으므로 후보에 없다.
        """
        preferred = self._param('decoder')
        fallback = self._param('decoder_fallback')

        registry = Gst.Registry.get()
        if registry.find_feature(preferred, Gst.ElementFactory.__gtype__):
            if preferred == 'nvv4l2decoder':
                # mjpeg=1이 MJPEG 입력을 NVJPG 하드웨어로 보낸다.
                return 'nvv4l2decoder mjpeg=1 ! nvvidconv'
            return f'{preferred} ! videoconvert'

        self.get_logger().warn(
            f'{preferred}를 찾지 못해 {fallback}(SW)로 폴백한다. '
            'CPU 사용률이 올라가므로 헤드룸을 다시 확인해야 한다.')
        return f'{fallback} ! videoconvert'

    def _has_element(self, name: str) -> bool:
        registry = Gst.Registry.get()
        return bool(registry.find_feature(name, Gst.ElementFactory.__gtype__))

    def _publish_sink_description(self) -> str:
        """MediaMTX로 보내는 sink를 고른다.

        rtspclientsink가 표준 경로지만 gstreamer1.0-rtsp 패키지가 필요하다.
        없으면 mpegtsmux + udpsink로 폴백한다. MediaMTX는 udp+mpegts://
        소스를 지원하므로 추가 패키지 없이 같은 결과를 낸다.
        """
        mode = str(self._param('publish_mode'))

        if mode == 'rtsp' and not self._has_element('rtspclientsink'):
            self.get_logger().warn(
                'rtspclientsink가 없어 udp_mpegts로 폴백한다. '
                '표준 경로를 쓰려면 sudo apt install -y gstreamer1.0-rtsp 후 재실행한다. '
                '폴백 시 MediaMTX 경로 source를 udp+mpegts로 맞춰야 한다.')
            mode = 'udp_mpegts'

        if mode == 'udp_mpegts':
            host = self._param('udp_host')
            port = int(self._param('udp_port'))
            self.publish_mode_effective = 'udp_mpegts'
            # MPEG-TS 먹싱이 한 단계 늘어난다. PoC-B 대비 오버헤드를 재확인해야 한다.
            return f'mpegtsmux ! udpsink host={host} port={port} sync=false'

        self.publish_mode_effective = 'rtsp'
        # protocols=tcp가 필수다. mediamtx.yml이 rtspTransports를 [tcp]로 제한하는데
        # rtspclientsink는 기본적으로 UDP를 먼저 시도해 핸드셰이크가 EOF로 끊긴다.
        return (
            f'rtspclientsink name=rtsp protocols=tcp latency=0 '
            f'location={self._param("rtsp_url")}'
        )

    def _pipeline_description(self) -> str:
        fps = int(self._param('input_framerate'))
        decoder = self._decoder_description()
        publish_sink = self._publish_sink_description()

        # 녹화 분기가 꺼져 있으면 tee를 막지 않도록 fakesink로 흘려버린다.
        # tee의 한 분기를 비워두면 파이프라인이 PREROLL에서 멈춘다.
        record_sink = (
            f'queue name=record_queue max-size-buffers='
            f'{int(self._param("record_queue_buffers"))} leaky=no ! fakesink sync=false'
        )
        if self._param('enable_record_branch') and self.ring is not None:
            # S15P11A301-123. 1초 MPEG-TS 조각으로 링 버퍼에 기록한다(32-5).
            record_sink = self.ring.sink_description(
                int(self._param('record_queue_buffers'))
            )

        return (
            f'appsrc name=src is-live=true do-timestamp=false format=time '
            f'  caps=image/jpeg,framerate={fps}/1 '
            f'! jpegparse '
            f'! {decoder} '
            f'! video/x-raw,format=I420 '
            f'! x264enc name=enc '
            f'    bitrate={int(self._param("encoder_bitrate_kbps"))} '
            f'    speed-preset={self._param("encoder_speed_preset")} '
            f'    tune={self._param("encoder_tune")} '
            f'    key-int-max={int(self._param("encoder_key_int_max"))} '
            f'    bframes={int(self._param("encoder_bframes"))} '
            f'    option-string=scenecut=0:open-gop=0 '
            f'! h264parse config-interval=1 '
            f'! video/x-h264,profile=baseline,alignment=au,stream-format=byte-stream '
            f'! tee name=t '
            f't. ! queue name=stream_queue max-size-buffers='
            f'{int(self._param("stream_queue_buffers"))} leaky=downstream '
            f'   ! {publish_sink} '
            f't. ! {record_sink}'
        )

    def _build_pipeline(self) -> None:
        description = self._pipeline_description()
        self.get_logger().info(f'파이프라인: {description}')

        self.pipeline = Gst.parse_launch(description)
        self.appsrc = self.pipeline.get_by_name('src')

        # bus.add_signal_watch()는 GLib 메인루프가 돌아야 메시지를 전달한다.
        # rclpy.spin()은 GLib 루프를 돌리지 않으므로 시그널이 영원히 오지 않는다.
        # 따라서 ROS 타이머에서 버스를 직접 폴링한다.
        self.bus = self.pipeline.get_bus()

        # 링 writer는 format-location-full 시그널로 조각 메타데이터를 얻는다.
        # 파이프라인을 다시 세울 때마다 연결해야 한다. 재구성 후 연결을 빠뜨리면
        # 조각 파일은 생기는데 index.json이 갱신되지 않아, 녹화 노드가 존재하는
        # 조각을 보지 못한다.
        if self.ring is not None:
            if self.ring.attach(self.pipeline):
                self.get_logger().info('링 writer 연결됨')
            else:
                self.get_logger().error(
                    'splitmuxsink를 찾지 못했다. 조각이 기록되지 않는다.'
                )

        self.pipeline.set_state(Gst.State.PLAYING)
        self.get_logger().info('파이프라인 PLAYING')

    def _create_input_subscription(self) -> None:
        # 발행자(usb_cam)는 RELIABLE/VOLATILE이다(62에서 확정).
        # 불일치하면 연결 자체가 되지 않는다.
        qos = QoSProfile(
            history=HistoryPolicy.KEEP_LAST,
            depth=5,
            reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.VOLATILE,
        )
        self.create_subscription(
            CompressedImage, self._param('input_topic'), self._on_frame, qos)

    # ------------------------------------------------------------------
    # 프레임 처리
    # ------------------------------------------------------------------
    def _stamp_ns(self, msg: CompressedImage) -> int:
        return msg.header.stamp.sec * 1_000_000_000 + msg.header.stamp.nanosec

    def _on_frame(self, msg: CompressedImage) -> None:
        if self.appsrc is None:
            return

        stamp_ns = self._stamp_ns(msg)
        self.last_frame_wall = self.get_clock().now()

        if self.first_stamp_ns is None:
            self.first_stamp_ns = stamp_ns
            self.stream_started = True
            if self.ring is not None:
                self.ring.reset_liveness()
            self.get_logger().info('첫 프레임 수신, PTS 기준을 0으로 설정')
        elif self.last_stamp_ns is not None and stamp_ns < self.last_stamp_ns:
            # stamp가 뒤로 갔다. 카메라 재시작이나 시각 점프다.
            # 리베이스하고 세그먼트 경계를 알린다(62 계약).
            self._rebase_pts(stamp_ns, reason='stamp_regression')

        self.last_stamp_ns = stamp_ns

        buffer = Gst.Buffer.new_wrapped(bytes(msg.data))
        buffer.pts = max(0, stamp_ns - self.first_stamp_ns)
        buffer.duration = Gst.CLOCK_TIME_NONE

        # 조각 메타데이터의 firstPts 기준. mpegtsmux를 지난 값은 1시간 오프셋이
        # 붙으므로 입력 PTS를 따로 알려준다(32-5 스트림 PTS).
        if self.ring is not None:
            self.ring.note_input_pts(buffer.pts)

        result = self.appsrc.emit('push-buffer', buffer)
        if result != Gst.FlowReturn.OK:
            self.get_logger().warn(f'appsrc push-buffer 실패: {result.value_nick}')
            return

        self.frames_pushed += 1
        self.restart_attempts = 0
        self.fault_declared = False

    def _rebase_pts(self, stamp_ns: int, reason: str) -> None:
        """PTS 기준을 다시 잡고 세그먼트 경계를 발행한다.

        리베이스는 반드시 새 세그먼트 경계를 강제한다. muxer가 시간
        불연속을 조각 경계로만 겪게 해야 재생 호환성 문제를 막는다(62 계약).
        """
        self.first_stamp_ns = stamp_ns
        self.get_logger().warn(f'PTS 리베이스 ({reason}). 세그먼트 경계를 발행한다.')
        message = String()
        message.data = reason
        self.boundary_pub.publish(message)

        # 링 writer가 같은 프로세스에 있으면 토픽을 기다리지 않고 직접 마감한다.
        # 토픽을 거치면 다음 조각에 불연속이 섞일 수 있다.
        if self.ring is not None and not self.ring.split_now():
            self.get_logger().warn(
                'splitmuxsink에 split-now를 보내지 못했다. '
                '시간 불연속이 조각 중간에 들어갈 수 있다.'
            )

    # ------------------------------------------------------------------
    # 입력 감시와 재시작
    # ------------------------------------------------------------------
    def _check_input_alive(self) -> None:
        # 아직 한 프레임도 못 받았으면 감시하지 않는다. 이미 받은 뒤라면
        # 재구성으로 first_stamp_ns가 비었어도 계속 감시해야 한다.
        if self.fault_declared or not self.stream_started:
            return
        if self.pending_restart:
            return

        timeout = float(self._param('input_timeout_seconds'))
        elapsed = (self.get_clock().now() - self.last_frame_wall).nanoseconds / 1e9
        if elapsed < timeout:
            return

        backoff = list(self._param('restart_backoff_seconds'))
        max_attempts = int(self._param('restart_max_attempts'))

        if self.restart_attempts >= max_attempts:
            self._declare_camera_fault()
            return

        delay = backoff[min(self.restart_attempts, len(backoff) - 1)]
        self.restart_attempts += 1
        self.get_logger().warn(
            f'입력이 {elapsed:.1f}초간 없다. '
            f'{delay}초 후 재시작 ({self.restart_attempts}/{max_attempts})')
        self.last_frame_wall = self.get_clock().now()
        if not self.pending_restart:
            self.pending_restart = True
            self.restart_timer = self.create_timer(delay, self._do_restart)

    def _check_ring_alive(self) -> None:
        """입력은 살아 있는데 링 조각만 멎은 교착을 복구한다.

        splitmuxsink 내부 경고는 GstBus ERROR가 아니라 GLib 경고로만 나올 수
        있다. 프로세스도 종료되지 않아 systemd의 Restart=on-failure가 보지
        못한다. 그래서 마지막 조각 시작 시각을 직접 감시한다.
        """
        if self.ring is None or not self.stream_started or self.pending_restart:
            return

        input_timeout = float(self._param('input_timeout_seconds'))
        input_age = (
            self.get_clock().now() - self.last_frame_wall
        ).nanoseconds / 1e9
        if input_age >= input_timeout:
            # 카메라 입력 장애는 _check_input_alive가 담당한다.
            return

        timeout = float(self._param('ring_stall_timeout_seconds'))
        if not self.ring.is_stalled(timeout):
            return

        age = self.ring.segment_age_seconds()
        age_text = '알 수 없음' if age is None else f'{age:.1f}초'
        self.get_logger().error(
            f'RING_STALL: 카메라 입력은 계속되지만 새 조각이 {age_text}간 '
            '열리지 않았다. 파이프라인을 재구성한다.'
        )
        message = String()
        message.data = 'RING_STALL'
        self.status_pub.publish(message)
        self._schedule_sink_restart('ring_stall')

    def _declare_camera_fault(self) -> None:
        """재시도를 다 쓰면 CAMERA_FAULT를 알리고 멈춘다.

        무한 재시작은 하지 않는다. 자율 탐사를 PAUSED로 전환하는 것은
        Mission Manager 책임이며 이 노드는 상태만 알린다(32-3).
        """
        self.fault_declared = True
        self.get_logger().error(
            'CAMERA_FAULT: 재시도 한도를 넘었다. 자율 탐사를 PAUSED로 전환해야 한다.')
        message = String()
        message.data = 'CAMERA_FAULT'
        self.status_pub.publish(message)

    # ------------------------------------------------------------------
    # GStreamer 버스 폴링
    # ------------------------------------------------------------------
    def _poll_bus(self) -> None:
        """버스를 비우고 오류를 처리한다.

        시그널 워치 대신 폴링을 쓰는 이유는 GLib 메인루프가 없기 때문이다.
        rclpy.spin()은 ROS 실행자만 돌리므로 시그널이 전달되지 않는다.
        """
        if self.bus is None:
            return

        mask = (Gst.MessageType.ERROR | Gst.MessageType.EOS
                | Gst.MessageType.WARNING)
        while True:
            message = self.bus.pop_filtered(mask)
            if message is None:
                return

            if message.type == Gst.MessageType.ERROR:
                error, debug = message.parse_error()
                source = message.src.get_name() if message.src else 'unknown'
                self.get_logger().error(
                    f'GStreamer 오류 [{source}]: {error.message} / {debug}')
                if self._disable_audio_on_error(source):
                    # 오디오만 문제다. 끄고 다시 세우면 비디오는 계속 기록된다.
                    # 이 분기가 없으면 마이크가 없는 기기에서 파이프라인 재구성이
                    # 무한히 반복되고 녹화가 영구히 멈춘다(S15P11A301-131).
                    self.get_logger().warn(
                        '오디오 없이 다시 세운다. 이벤트 영상에 소리가 담기지 '
                        '않지만 녹화와 스트리밍은 유지된다.'
                    )
                self._schedule_sink_restart(source)
            elif message.type == Gst.MessageType.EOS:
                self.get_logger().warn('GStreamer EOS. 파이프라인을 재시작한다.')
                self._schedule_sink_restart('eos')
            else:
                warning, debug = message.parse_warning()
                self.get_logger().warn(
                    f'GStreamer 경고: {warning.message} / {debug}')

    def _disable_audio_on_error(self, source: str) -> bool:
        """오디오 요소가 낸 오류면 오디오를 끈다. 껐으면 True.

        마이크가 없거나 다른 프로세스가 배타적으로 잡고 있으면 `pulsesrc`가
        PLAYING 전환에서 실패한다. 그때 오디오를 그대로 두고 재구성하면 같은
        실패가 반복되고, 파이프라인이 서지 못해 녹화까지 멈춘다.

        `RingBufferWriter`를 재구성 때 새로 만들지 않는다는 점이 이 방식의
        전제다. 그것은 `__init__`에서 한 번만 만들어지고 `_build_pipeline`은
        `sink_description()`만 다시 부른다. 그래서 여기서 끈 값이 재구성 뒤에도
        남고 같은 실패를 되풀이하지 않는다. 만약 `_build_pipeline`이 writer를
        다시 만들게 바뀌면 `enable_audio` 파라미터가 되살아나 무한 반복이 된다.
        """
        if self.ring is None or not self.ring.audio_enabled:
            return False
        if not is_audio_element(source):
            return False
        self.ring.audio_enabled = False
        return True

    def _schedule_sink_restart(self, reason: str) -> None:
        """출력 경로 장애로 파이프라인을 다시 세운다.

        MediaMTX가 죽고 respawn되면 rtspclientsink 연결이 끊긴다. 카메라
        프레임은 계속 들어오므로 입력 감시 타이머로는 잡히지 않는다. 이 경로가
        없으면 MediaMTX 재시작 이후 스트림이 영구히 복구되지 않는다.

        카메라 결함과 별개로 세므로 CAMERA_FAULT를 올리지 않는다.
        """
        if self.pending_restart:
            return

        backoff = list(self._param('restart_backoff_seconds'))
        delay = backoff[min(self.sink_restart_attempts, len(backoff) - 1)]
        self.sink_restart_attempts += 1
        self.pending_restart = True

        self.get_logger().warn(
            f'출력 경로 장애({reason}). {delay}초 후 파이프라인을 다시 세운다 '
            f'(누적 {self.sink_restart_attempts}회).')

        # 일회성 ROS 타이머. GLib.timeout_add는 메인루프가 없어 발화하지 않는다.
        self.restart_timer = self.create_timer(delay, self._do_restart)

    def _do_restart(self) -> None:
        if self.restart_timer is not None:
            self.restart_timer.cancel()
            self.destroy_timer(self.restart_timer)
            self.restart_timer = None

        self.get_logger().warn('파이프라인 재구성')
        if self.pipeline is not None:
            self.pipeline.set_state(Gst.State.NULL)

        self.first_stamp_ns = None
        self.last_stamp_ns = None
        self._build_pipeline()
        self.pending_restart = False
        self.last_frame_wall = self.get_clock().now()

        # 재구성은 시간 불연속을 만든다. 링 writer가 조각을 끊도록 알린다.
        message = String()
        message.data = 'pipeline_restart'
        self.boundary_pub.publish(message)

    def destroy_node(self) -> bool:
        # 열린 조각을 닫아 index.json을 마무리한다. 하지 않으면 마지막 조각이
        # 인덱스에 없어 녹화 노드가 그 구간을 찾지 못한다.
        if self.ring is not None:
            self.ring.close()
        if self.pipeline is not None:
            self.pipeline.set_state(Gst.State.NULL)
        self.get_logger().info(f'종료. 누적 push 프레임 {self.frames_pushed}')
        return super().destroy_node()


def main(args=None) -> None:
    rclpy.init(args=args)
    node = StreamPipelineNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
