package com.sentinel.backend.mission.dto;

import java.time.Instant;

/**
 * 시계열 그래프의 한 점. robot_metrics·environment_metrics·robot_pose 를 같은
 * time_bucket 격자로 구간 집계해 합친 값이다 (S15P11A301-205).
 *
 * <p>null 은 "모름"이고 0 은 값이다(젯슨 계약). 센서가 빠졌거나 그 구간에 값이
 * 없으면 null 로 나가고, 프론트는 결측으로 그려야 한다. battery 는 전압 계측이
 * 없어(#174) 당분간 항상 null 이다 — 값이 올 전제의 로직을 얹지 말 것.
 *
 * <p>mcuConnected 는 구간 bool_and: 한 번이라도 끊겼으면 false, 보고가 없었으면 null.
 */
public record TelemetryPointResponse(
        Instant time,
        Double cpu,
        Double gpu,
        Double memory,
        Double jetsonTemp,
        Double battery,
        Double temperature,
        Double humidity,
        Double linearVelocity,
        Double angularVelocity,
        Boolean mcuConnected) {
}
