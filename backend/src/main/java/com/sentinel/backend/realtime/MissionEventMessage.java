package com.sentinel.backend.realtime;

import java.time.Instant;
import java.util.UUID;

/**
 * {@code /topic/missions/{id}/events} 메시지 (31-8: 사람 탐지·오류·E-Stop·임무 상태).
 * 지금은 임무 상태 전이(MISSION_STATUS)만 보낸다 — 나머지 유형은 후속 범위.
 */
public record MissionEventMessage(
        String type,
        UUID missionId,
        String status,
        Instant at) {
}
