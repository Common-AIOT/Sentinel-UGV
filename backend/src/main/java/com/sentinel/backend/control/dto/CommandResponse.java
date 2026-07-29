package com.sentinel.backend.control.dto;

import java.util.UUID;

/**
 * 명령 접수 응답 (27.4). 202 와 함께 반환하며, status 는 항상 PENDING 으로 시작한다.
 * 이후 결과는 젯슨의 COMMAND_ACK 가 {@code control_commands.result} 에 반영한다.
 */
public record CommandResponse(
        UUID commandId,
        String status
) {
}
