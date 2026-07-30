package com.sentinel.backend.encounter.dto;

import java.time.Instant;
import java.util.UUID;

/**
 * 임무별 발견 목록 한 행 (명세 27.4 {@code GET /api/v1/missions/{id}/encounters}).
 *
 * <p>지도 마커와 발견 목록 화면에 필요한 최소 필드만 담는다. 영상 연결은 상세 조회가 준다.
 */
public record EncounterSummaryResponse(
        UUID id,
        String status,
        Double mapX,
        Double mapY,
        Double mapYaw,
        Integer detectedPersonCount,
        Instant startedAt,
        Instant endedAt,
        String terminationReason) {
}
