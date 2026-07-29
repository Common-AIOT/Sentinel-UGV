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
}
