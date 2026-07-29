package com.sentinel.backend.messaging.dto;

import java.time.Instant;
import java.util.UUID;

import tools.jackson.databind.JsonNode;

/**
 * 젯슨과 공유하는 공통 메시지 봉투 (명세 31-5, {@code common/schemas/envelope.schema.json}).
 *
 * <p>{@code data} 는 {@code messageType} 에 따라 본문 스키마가 달라지므로 트리로 받고,
 * 라우팅 후 각 타입의 record 로 변환한다.
 *
 * <p>{@code missionId} 는 임무 외 상태에서 null 이다.
 */
public record MessageEnvelope(
        String schemaVersion,
        UUID messageId,
        String messageType,
        String robotId,
        UUID missionId,
        long sequence,
        Instant sentAt,
        JsonNode data
) {
    public static final String TYPE_TELEMETRY = "ROBOT_TELEMETRY";
    public static final String TYPE_PRESENCE = "ROBOT_PRESENCE";
    public static final String TYPE_ENCOUNTER = "ENCOUNTER_CONFIRMED";
}
