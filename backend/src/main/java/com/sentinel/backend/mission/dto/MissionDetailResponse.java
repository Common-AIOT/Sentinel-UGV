package com.sentinel.backend.mission.dto;

import java.time.Instant;
import java.util.UUID;

import tools.jackson.databind.JsonNode;

/**
 * 임무 상세·요약 (명세 27.4 {@code GET /api/v1/missions/{id}}).
 *
 * <p>homePose 는 {@code missions.home_pose}(JSONB) 를 그대로 내려준다. 임무 시작 전에는 null 이다.
 */
public record MissionDetailResponse(
        UUID id,
        String robotId,
        String status,
        Instant startedAt,
        Instant endedAt,
        String endReason,
        Instant createdAt,
        JsonNode homePose,
        Integer durationSec,
        Double distanceM,
        Double coverage,
        Integer detectionCount) {
}
