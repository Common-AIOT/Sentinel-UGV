package com.sentinel.backend.mission.dto;

import java.time.Instant;
import java.util.UUID;

/**
 * 임무 목록 한 행 (명세 28.1 임무 목록: 시간·상태·탐지 수·거리·종료 사유).
 *
 * <p>durationSec·distanceM·detectionCount 는 {@code mission_results} 에서 오며,
 * 임무가 끝나기 전에는 null 이다.
 */
public record MissionSummaryResponse(
        UUID id,
        String robotId,
        String status,
        Instant startedAt,
        Instant endedAt,
        String endReason,
        Instant createdAt,
        Integer durationSec,
        Double distanceM,
        Integer detectionCount) {
}
