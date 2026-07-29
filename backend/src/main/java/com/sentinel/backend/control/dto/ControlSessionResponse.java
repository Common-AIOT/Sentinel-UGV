package com.sentinel.backend.control.dto;

import java.time.Instant;
import java.util.UUID;

/**
 * 제어권 세션 응답 (11.4). 명칭은 controlSessionId/control-sessions 계열로 통일한다.
 */
public record ControlSessionResponse(
        UUID sessionId,
        String robotId,
        Instant expiresAt
) {
}
