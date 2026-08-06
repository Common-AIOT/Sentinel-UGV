package com.sentinel.backend.messaging.dto;

import java.util.UUID;

/**
 * MISSION_COMMAND 본문 (명세 31-4 {@code cmd/mission}, {@code common/schemas/mission-command.schema.json}).
 *
 * <p>서버가 발행하는 유일한 봉투 본문이다. 대상 임무는 봉투의 missionId 로 전달하고,
 * 젯슨은 commandId 로 QoS 1 중복을 멱등 처리한다.
 */
public record MissionCommandData(
        UUID commandId,
        String type
) {
    public static final String TYPE_START = "START";
    public static final String TYPE_PAUSE = "PAUSE";
    public static final String TYPE_RESUME = "RESUME";
    public static final String TYPE_RETURN = "RETURN";
    public static final String TYPE_STOP = "STOP";
    /** 수동 전환. 젯슨이 모터 보드에 SET_MODE(MANUAL) 을 보낸다 (S15P11A301-298). */
    public static final String TYPE_MANUAL = "MANUAL";
    /**
     * 자율 복귀. 모터 보드가 **거부할 수 있는 유일한 명령**이다 — 최근 500ms 안에
     * 모바일 조종 입력이 있었으면 {@code REJECTED/MANUAL_INPUT_ACTIVE} 로 회신한다.
     */
    public static final String TYPE_AUTO = "AUTO";
}
