package com.sentinel.backend.messaging.dto;

import java.time.Instant;
import java.util.List;
import java.util.UUID;

/**
 * 음성 상호작용 보고 본문
 * ({@code common/schemas/interaction-report.schema.json}, S15P11A301-159).
 */
public record InteractionReportData(
        UUID interactionId,
        UUID encounterId,
        UUID missionId,
        int visionPersonCount,
        Instant startedAt,
        Instant endedAt,
        SessionReport sessionReport,
        RiskAssessment riskAssessment,
        boolean usedFallback
) {
    public record SessionReport(
            String responseScope,
            Boolean anyResponseDetected,
            Integer reportedResponsiveCount,
            String reportedCountStatus,
            Double countConfidence,
            String mobilityStatus,
            String urgentConditionReported,
            boolean operatorReviewRequired,
            String terminationReason
    ) {
    }

    public record RiskAssessment(
            String riskLevel,
            List<String> riskReasons,
            String ruleVersion,
            boolean operatorReviewRequired
    ) {
    }
}
