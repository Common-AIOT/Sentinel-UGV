package com.sentinel.backend.encounter.dto;

import java.time.Instant;
import java.util.List;
import java.util.UUID;

/**
 * 발견 상세 (명세 27.4 {@code GET /api/v1/encounters/{encounterId}}, 31-7).
 *
 * <p>responsive·unresponsive 인원 수와 interactionSummary 는 상호작용 연계(MVP 범위 밖)가
 * 채우기 전까지 null 이다. media 는 이 encounter 에 연결된 영상·썸네일 목록이다.
 */
public record EncounterDetailResponse(
        UUID id,
        UUID missionId,
        String status,
        Double mapX,
        Double mapY,
        Double mapYaw,
        Integer detectedPersonCount,
        Integer responsivePersonCount,
        Integer unresponsivePersonCount,
        String interactionSummary,
        EncounterPose encounterPose,
        List<AdditionalPersonReport> additionalPersonReports,
        Instant startedAt,
        Instant interactionStartedAt,
        Instant interactionEndedAt,
        Instant endedAt,
        String terminationReason,
        List<EncounterMediaResponse> media) {

    public record EncounterPose(
            double x,
            double y,
            double yaw,
            String mapId) {
    }

    public record AdditionalPersonReport(
            String subjectText,
            Integer reportedCount,
            String countStatus,
            String locationText,
            Integer reportedFloor,
            String groundingStatus,
            String responseStatus,
            String certaintyStatus,
            String rawUtterance,
            String verificationStatus,
            boolean operatorReviewRequired) {
    }
}
