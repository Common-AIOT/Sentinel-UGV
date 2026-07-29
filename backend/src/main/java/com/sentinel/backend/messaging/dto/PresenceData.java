package com.sentinel.backend.messaging.dto;

/**
 * ROBOT_PRESENCE 본문 (명세 31-4, {@code common/schemas/presence.schema.json}).
 *
 * <p>OFFLINE 은 젯슨의 정상 종료(SHUTDOWN)와 브로커가 대신 발행하는 Last Will
 * (MQTT_CONNECTION_LOST) 두 경로로 온다. LWT 의 {@code sentAt} 은 접속 시점 시각이므로
 * 단절 시각으로 쓸 수 없고, 서버 수신 시각을 사용한다.
 */
public record PresenceData(
        String robotId,
        String status,
        String reason
) {
    public static final String STATUS_ONLINE = "ONLINE";
    public static final String STATUS_OFFLINE = "OFFLINE";
}
