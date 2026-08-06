package com.sentinel.backend.messaging.dto;

import java.util.Map;
import java.util.UUID;

/**
 * ROBOT_STATE 본문 (명세 31-4 {@code state}, {@code common/schemas/state.schema.json}).
 *
 * <p>젯슨이 상태 변경 시 즉시, 그리고 1초 heartbeat 로 발행한다. 종전에는 서버가 이
 * messageType 을 유일하게 **버리고** 있었다({@code MqttGateway} 의 default 분기,
 * S15P11A301-298 에서 발견). 그래서 명령 없이 일어나는 상태 변화 — 사람이 폰을 잡아
 * 모터 보드가 수동으로 승격하는 경우 — 가 관제에 전혀 보이지 않았다. {@code commandId}
 * 가 없어 {@code CommandAckWriter} 경로를 타지 않기 때문이다.
 *
 * <p>{@code missionState} 는 임무 상태 머신(26.2)의 값이고 {@code safetyState} 와 다른
 * 상태 공간이다. 서버는 이 값을 {@code missions.status} 에 반영하며, 그것이 관제 화면의
 * 임무 상태 표시의 근거다 — 명령 이력이 아니라 로봇의 보고가 「지금 무엇을 하고 있나」를
 * 정한다({@code RobotStateWriter}, S15P11A301-316).
 */
public record RobotStateData(
        String robotId,
        String missionState,
        String controlMode,
        String safetyState,
        UUID activeMissionId,
        Map<String, Boolean> components
) {
    public static final String MISSION_STATE_MANUAL = "MANUAL";
}
