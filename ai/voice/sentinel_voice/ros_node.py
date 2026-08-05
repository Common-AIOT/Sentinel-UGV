"""비전 Encounter와 음성 세션을 Mission Manager·cloud bridge에 연결한다.

구독:
  /perception/encounter  APPROACHED에서 encounter당 한 번 음성 세션 시작
  /mission/status        ESTOP/ERROR/MANUAL/PAUSED에서 진행 중 세션 중단
                         임무 상태를 보관해 종료 안내의 탐사 문구 사용 여부를 판단

발행:
  /interaction/report    구조화 보고를 cloud bridge에 인계
  /mission/signal        종료 안내 재생을 마친 뒤 DIALOGUE_ENDED 통지

세션 종료 안내는 말하는 시점에 참인 문구만 재생한다.

  종료 안내는 단일 문구(REPORT_SUCCEEDED_DEPARTURE)다 — 146 v2, 실패 없음 가정.
  임무 상태가 재개를 약속할 수 없으면 재생하지 않고 기록만 남긴다.
  (관제 ACK는 로봇 다수 투입으로 존재하지 않는다 — 2026-08-01 확정)

안내를 DIALOGUE_ENDED보다 먼저 재생한다. 즉시 재개 정책에서 순서를 뒤집으면
로봇이 멀어지며 마지막 안내를 하게 되어 요구조자가 듣지 못한다.

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
from .guide_audio import GUIDE_ASSETS, GuideCode
from .integration import (
    build_interaction_report,
    dialogue_ended_signal,
    mission_abort_state,
    mission_resume_expected,
    utc_now_iso,
)
from .report_delivery import DeliveryResult, queue_report
from .safety import report_defaults
from .session_log import SessionLog


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
        self._mission_state: str | None = None
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

        # 세션 종료 안내에서 탐사 문구를 쓸 수 있는지 판단할 근거를 기록한다.
        # 중단 판정보다 먼저 저장해야 한다. 중단이 아닌 상태는 아래에서 반환된다.
        with self._lock:
            self._mission_state = payload.get("state")

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
        session_log = SessionLog(None)
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
                result = runner.run(source="VISION")
                used_fallback = runner.used_fallback
                session_log = runner.session_log
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
            session_log.report({"error": str(error)})
            delivery = queue_report({}, lambda _report: False)
        else:
            # 관제로 나간 것과 같은 본문을 남긴다. 발신 실패 시 대조에 쓴다.
            session_log.report(payload)
            delivery = queue_report(payload, self._publish_report)

        self.get_logger().info(
            f"음성 보고 인계: {delivery.state.value} ({delivery.detail})"
        )

        # 안내를 먼저 끝내고 DIALOGUE_ENDED를 발행한다. 순서를 뒤집으면 Mission
        # Manager가 즉시 탐사를 재개해, 로봇이 멀어지며 모터 소음 속에서 마지막
        # 안내를 하게 되고 요구조자가 그것을 듣지 못한다.
        self._announce_delivery(delivery, session_log)

        signal = dialogue_ended_signal(
            context, termination_reason=result.termination_reason
        )
        self.signal_pub.publish(
            String(data=json.dumps(signal, ensure_ascii=False))
        )
        self.get_logger().info(
            f"DIALOGUE_ENDED 발행 encounter={context.encounter_id[:8]}"
        )

    def _announce_delivery(
        self, delivery: DeliveryResult, session_log: SessionLog
    ) -> None:
        """세션의 마지막 안내를 재생한다. 실패는 기록만 하고 노드를 죽이지 않는다.

        상태머신은 발신 상태를 알 수 없어 종료 안내를 하지 않는다. 이 안내가
        세션에서 유일한 종료 안내다.

        아직 DIALOGUE_ENDED를 보내지 않아 재개를 관측할 수 없으므로, 탐사 문구는
        임무 상태가 중단·정지·종료가 아님을 확인하고 쓴다.
        """

        code = delivery.guide_code
        resume_expected = False
        if code == GuideCode.REPORT_SUCCEEDED_DEPARTURE:
            with self._lock:
                state = self._mission_state
                aborted = self._abort_state is not None
            resume_expected = not aborted and mission_resume_expected(state)
            if not resume_expected:
                # 재개를 약속할 수 없으면 탐사 문구를 재생하지 않는다.
                # 진행형 대체 문구(REPORT_PENDING)는 146 v2에서 삭제됐다 —
                # 지킬 수 없는 약속 대신 침묵을 택하고 기록만 남긴다.
                self.get_logger().info(
                    f"탐사 재개를 약속하지 않는다 (임무 상태 {state}) — "
                    "종료 안내 생략"
                )
                session_log.announcement(
                    code.value, "SKIPPED", f"탐사 재개 약속 불가 ({state})"
                )
                return
        try:
            from .pipeline import guide_player

            result = guide_player.play(
                code, exploration_resume_approved=resume_expected
            )
        except Exception as error:  # 오디오 장치 오류가 노드를 멈추게 하지 않는다.
            self.get_logger().warn(
                f"안내 음성 재생 예외: {type(error).__name__}: {error}"
            )
            session_log.announcement(code.value, "EXCEPTION", type(error).__name__)
            return
        session_log.announcement(code.value, result.status.value, result.detail)
        if result.ok:
            self.get_logger().info(f"안내 음성 재생: {code.value}")
        else:
            self.get_logger().warn(
                f"안내 음성 재생 실패: {result.status.value} ({result.detail})"
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
