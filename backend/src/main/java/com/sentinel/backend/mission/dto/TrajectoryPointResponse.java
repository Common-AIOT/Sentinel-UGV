package com.sentinel.backend.mission.dto;

import java.time.Instant;

/**
 * 궤적의 한 점 (명세 27.4 {@code GET /missions/{id}/trajectory}, S15P11A301-194).
 * 좌표는 map 좌표계 기준 미터, yaw 는 라디안(REP-103)이다 — 지도(yaml origin)와 같은 기준.
 */
public record TrajectoryPointResponse(
        Instant time,
        double x,
        double y,
        double yaw) {
}
