package com.sentinel.backend.control.dto;

import java.time.Instant;
import java.util.UUID;

/**
 * 명령 처리 결과 한 건 (S15P11A301-207, 명세 27.6 "명령 요청부터 ACK 까지 추적").
 *
 * <p>result 는 젯슨 ACK 의 상태(ACCEPTED/EXECUTED/REJECTED/EXPIRED/FAILED)이고,
 * ACK 가 아직 없으면 PENDING 이다 — 발행 응답({@link CommandResponse})과 같은 어휘라
 * 관제가 두 API 를 하나의 상태 흐름으로 읽을 수 있다.
 *
 * <p>reasonCode 는 거부·실패의 이유(ROBOT_BUSY·NOT_IMPLEMENTED 등)로, 성공이거나
 * 회신 전이면 null 이다.
 */
public record CommandStatusResponse(
        UUID commandId,
        String type,
        String result,
        String reasonCode,
        Instant requestedAt) {
}
