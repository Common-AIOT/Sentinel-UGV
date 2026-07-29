package com.sentinel.backend.messaging.dto;

import java.time.Instant;
import java.util.List;
import java.util.UUID;

/**
 * ENCOUNTER_CONFIRMED 본문 (명세 32-5·32-6, {@code common/schemas/encounter.schema.json}).
 *
 * <p>phase 가 녹화 상태 머신을 움직이는 신호이며, 같은 {@code encounterId} 로 여러 phase 가
 * 순서대로 온다. 사람 수·위치는 보고서용이다.
 *
 * <p>{@code missionId} 는 봉투에도 있고 본문에도 있다. ENDED 처럼 임무 정보가 빠진 채 오는
 * 메시지가 있으므로(예제 encounter-ended.json) 신규 적재 시에만 둘 중 하나를 쓴다.
 */
public record EncounterData(
        UUID encounterId,
        String phase,
        Instant detectedAt,
        Integer personCount,
        List<Integer> trackIds,
        Double confidence,
        Pose pose,
        UUID missionId
) {
    public static final String PHASE_CONFIRMED = "CONFIRMED";
    public static final String PHASE_APPROACHED = "APPROACHED";
    public static final String PHASE_ENDED = "ENDED";
    public static final String PHASE_REDETECTED = "REDETECTED";
    public static final String PHASE_LOST = "LOST";

    /** 확정 시점의 로봇 위치. SLAM 이 없으면 null 이다. */
    public record Pose(Double x, Double y, Double yaw, String mapId) {
    }
}
