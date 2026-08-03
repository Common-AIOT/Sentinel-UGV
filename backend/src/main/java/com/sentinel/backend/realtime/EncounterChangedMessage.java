package com.sentinel.backend.realtime;

import java.time.Instant;
import java.util.UUID;

/**
 * {@code /topic/missions/{id}/encounters} 메시지 (31-8: 인원 수·상호작용 상태).
 * 발견의 생성·phase 변화를 알린다. 상세가 필요하면 관제가 REST 상세 조회로 이어간다.
 */
public record EncounterChangedMessage(
        UUID encounterId,
        String phase,
        Integer personCount,
        Double mapX,
        Double mapY,
        Instant detectedAt) {
}
