"""비전 Encounter와 음성 세션을 Mission Manager·cloud bridge에 연결한다.

구독:
  /perception/encounter  APPROACHED에서 encounter당 한 번 음성 세션 시작
  /mission/status        ESTOP/ERROR/MANUAL/PAUSED에서 진행 중 세션 중단

발행:
  /interaction/report    구조화 보고를 cloud bridge에 인계
  /mission/signal        정상 세션 종료 뒤 DIALOGUE_ENDED 통지

긴 오디오·GMS 처리는 ROS 콜백 스레드가 아니라 작업 스레드에서 수행한다.
"""

from __future__ import annotations

import json
import threading

import rclpy
from rclpy.node import Node
from rclpy.qos import (
    QoSDurabilityPolicy,
    QoSHistoryPolicy,
    QoSProfile,
    QoSReliabilityPolicy,
)
from std_msgs.msg import String

from .conversation import SessionResult, SessionState
from .encounter import (
    EncounterContractError,
    EncounterContext,
    EncounterSessionCoordinator,
    parse_encounter_message,
)
from .guide_audio import GUIDE_ASSETS
from .integration import (
    build_interaction_report,
    dialogue_ended_signal,
    mission_abort_state,
    utc_now_iso,
)
from .report_delivery import queue_report
from .safety import report_defaults


class VoiceSessionNode(Node):
    """ROS 2 음성 세션 통합 노드."""

    def __init__(self) -> None:
        super().__init__("voice_session")
        self.declare_parameter("encounter_topic", "/perception/encounter")
        self.declare_parameter("mission_status_topic", "/mission/status")
        self.declare_parameter("mission_signal_topic", "/mission/signal")
        self.declare_parameter("interaction_report_topic", "/interaction/report")

        event_qos = QoSProfile(
            reliability=QoSReliabilityPolicy.RELIABLE,
            history=QoSHistoryPolicy.KEEP_LAST,
            depth=10,
        )
        retained_qos = QoSProfile(
            reliability=QoSReliabilityPolicy.RELIABLE,
            durability=QoSDurabilityPolicy.TRANSIENT_LOCAL,
            history=QoSHistoryPolicy.KEEP_LAST,
            depth=10,
        )
        self.report_pub = self.create_publisher(
            String, self._param("interaction_report_topic"), retained_qos
        )
        self.signal_pub = self.create_publisher(
            String, self._param("mission_signal_topic"), event_qos
        )
        self.create_subscription(
            String,
            self._param("encounter_topic"),
            self._on_encounter,
            event_qos,
        )
        self.create_subscription(
            String,
            self._param("mission_status_topic"),
            self._on_mission_status,
            retained_qos,
        )

        self._coordinator = EncounterSessionCoordinator()
        self._lock = threading.Lock()
        self._abort_state: SessionState | None = None
        self._worker: threading.Thread | None = None
        self.get_logger().info(
            "voice_session 시작. APPROACHED 대기, 보고는 /interaction/report로 인계"
        )

    def _param(self, name: str) -> str:
        return str(self.get_parameter(name).value)

    def _on_encounter(self, message: String) -> None:
        try:
            context = parse_encounter_message(message.data)
        except EncounterContractError as error:
            self.get_logger().warn(f"encounter 계약 위반: {error}")
            return

        with self._lock:
            outcome = self._coordinator.handle(context)
            if outcome.abort_conversation:
                self._abort_state = SessionState.ABORTED_SAFETY
            start = outcome.start_conversation

        if outcome.accepted:
            self.get_logger().info(
                f"encounter {context.phase.value}: {outcome.detail}"
            )
        if start:
            self._start_worker(context)

    def _on_mission_status(self, message: String) -> None:
        try:
            payload = json.loads(message.data)
        except json.JSONDecodeError as error:
            self.get_logger().warn(f"mission status JSON 해석 실패: {error}")
            return

        abort_kind = mission_abort_state(payload)
        if abort_kind is None:
            return
        with self._lock:
            if abort_kind == "SAFETY":
                outcome = self._coordinator.abort_safety()
                abort_state = SessionState.ABORTED_SAFETY
            else:
                outcome = self._coordinator.abort_manual()
                abort_state = SessionState.ABORTED_MANUAL
            if outcome.abort_conversation:
                self._abort_state = abort_state
        if outcome.accepted:
            self.get_logger().warn(
                f"임무 상태 {payload.get('state')}: {outcome.detail}"
            )

    def _start_worker(self, context: EncounterContext) -> None:
        if self._worker is not None and self._worker.is_alive():
            self.get_logger().error("음성 작업이 이미 실행 중이라 중복 시작을 거부했다")
            return
        self._abort_state = None
        self._worker = threading.Thread(
            target=self._run_session,
            args=(context,),
            name=f"voice-{context.encounter_id[:8]}",
            daemon=True,
        )
        self._worker.start()

    def _requested_abort(self) -> SessionState | None:
        with self._lock:
            return self._abort_state

    def _run_session(self, context: EncounterContext) -> None:
        started_at = utc_now_iso()
        used_fallback = False
        try:
            # 무거운 오디오·Whisper 의존성은 실제 encounter가 시작될 때 로딩한다.
            from .pipeline import build_dependencies, guide_player
            from .session_gate import check_session_gate
            from .session_runner import VoiceSessionRunner

            gate = check_session_gate()
            if not gate.proceed:
                guide_player.play_text(GUIDE_ASSETS[gate.guide_code].text)
                result = SessionResult(
                    state=SessionState.COMPLETED,
                    fields=report_defaults(),
                    termination_reason="GMS_UNAVAILABLE",
                )
                result.fields["terminationReason"] = "GMS_UNAVAILABLE"
                self.get_logger().warn(
                    f"GMS 세션 게이트 차단: {gate.state.value}"
                )
            else:
                runner = VoiceSessionRunner(
                    build_dependencies(),
                    abort_requested=self._requested_abort,
                    on_event=self.get_logger().info,
                )
                result = runner.run()
                used_fallback = runner.used_fallback
        except Exception as error:  # 장치·모델 초기화 오류도 ROS 노드를 죽이지 않는다.
            self.get_logger().error(
                f"음성 세션 실행 실패: {type(error).__name__}: {error}"
            )
            result = SessionResult(
                state=SessionState.FAILED_AUDIO,
                fields=report_defaults(),
                termination_reason="AUDIO_DEVICE_ERROR",
            )
            result.fields["terminationReason"] = "AUDIO_DEVICE_ERROR"

        if result.state in {
            SessionState.ABORTED_MANUAL,
            SessionState.ABORTED_SAFETY,
        } or self._requested_abort() is not None:
            self.get_logger().warn(
                f"음성 세션 중단({result.termination_reason}); "
                "보고와 자동 재개 신호를 보내지 않는다"
            )
            return

        with self._lock:
            outcome = self._coordinator.conversation_finished(
                termination_reason=result.termination_reason or "UNKNOWN"
            )
        if not outcome.accepted:
            self.get_logger().warn(f"음성 결과를 폐기했다: {outcome.detail}")
            return

        try:
            payload = build_interaction_report(
                context,
                result,
                started_at=started_at,
                ended_at=utc_now_iso(),
                used_fallback=used_fallback,
            )
        except ValueError as error:
            self.get_logger().warn(str(error))
            delivery = queue_report({}, lambda _report: False)
        else:
            delivery = queue_report(payload, self._publish_report)

        self.get_logger().info(
            f"음성 보고 인계: {delivery.state.value} ({delivery.detail})"
        )
        signal = dialogue_ended_signal(
            context, termination_reason=result.termination_reason
        )
        self.signal_pub.publish(
            String(data=json.dumps(signal, ensure_ascii=False))
        )
        self.get_logger().info(
            f"DIALOGUE_ENDED 발행 encounter={context.encounter_id[:8]}"
        )

    def _publish_report(self, report: dict) -> bool:
        if not report.get("missionId"):
            return False
        self.report_pub.publish(
            String(data=json.dumps(report, ensure_ascii=False))
        )
        return True

    def shutdown(self) -> None:
        with self._lock:
            self._abort_state = SessionState.ABORTED_SAFETY


def main(args=None) -> None:
    rclpy.init(args=args)
    node = VoiceSessionNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.shutdown()
        try:
            node.destroy_node()
            if rclpy.ok():
                rclpy.shutdown()
        except KeyboardInterrupt:
            # launch가 종료 중 SIGINT를 한 번 더 전달해도 traceback을 남기지 않는다.
            pass


if __name__ == "__main__":
    main()
