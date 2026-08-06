package com.sentinel.backend.messaging.dto;

import java.time.Instant;
import java.time.temporal.ChronoUnit;
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
    public static final String TYPE_INTERACTION_REPORT = "INTERACTION_REPORT";
    public static final String TYPE_MISSION_COMMAND = "MISSION_COMMAND";
    public static final String TYPE_COMMAND_ACK = "COMMAND_ACK";

    public static final String SCHEMA_VERSION = "1.0";

    /**
     * 서버 발행 봉투를 만든다 (S15P11A301-288).
     *
     * <p>발행부에 흩어져 있던 규칙을 여기 모은다. 계약 시험(ServerPublishContractTest)이
     * 실제 발행 코드를 검사할 수 있어야 하기 때문이다 — 시험이 봉투를 따로 만들면
     * 발행부가 규칙을 어겨도 시험은 통과한다.
     *
     * <p>{@code sentAt} 을 밀리초로 절단하는 것이 규칙의 핵심이다. 봉투 스키마의 pattern 이
     * 소수점 이하 6자리까지만 허용하는데 Jackson 의 Instant 직렬화는 나노초(9자리)를 낸다.
     * MVP 주에 이 위반이 운영으로 나갔다. {@code sequence} 는 그 시각의 epoch millis 로
     * 단조 증가시킨다 — 서버는 젯슨과 달리 재시작해도 이어지는 카운터가 없다.
     */
    public static MessageEnvelope forPublish(
            String messageType, String robotId, UUID missionId, JsonNode data) {
        Instant now = Instant.now().truncatedTo(ChronoUnit.MILLIS);
        return new MessageEnvelope(
                SCHEMA_VERSION,
                UUID.randomUUID(),
                messageType,
                robotId,
                missionId,
                now.toEpochMilli(),
                now,
                data);
    }
}
