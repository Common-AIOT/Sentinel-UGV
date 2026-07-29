package com.sentinel.backend.messaging.dto;

import java.util.UUID;

/**
 * COMMAND_ACK 본문 (명세 31-6, {@code common/schemas/command-ack.schema.json}).
 *
 * <p>제어 API 의 HTTP 202 는 전달 시작일 뿐이고, 이 ACK 가 도착해야 수락·거부가
 * 확정된다(27.4). status: ACCEPTED/EXECUTED/REJECTED/EXPIRED/FAILED.
 */
public record CommandAckData(
        UUID commandId,
        String status,
        String reasonCode,
        String message
) {
    public static final String STATUS_ACCEPTED = "ACCEPTED";
    public static final String STATUS_EXECUTED = "EXECUTED";
}
