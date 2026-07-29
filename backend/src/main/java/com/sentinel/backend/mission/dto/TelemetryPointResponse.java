package com.sentinel.backend.mission.dto;

import java.time.Instant;

/**
 * 시계열 그래프의 한 점. {@code robot_metrics} 를 time_bucket 으로 구간 평균한 값이다.
 *
 * <p>ESP32 미연동 동안 battery 는 null 로 온다. 그 구간 평균도 null 이므로 프론트는
 * null 을 결측으로 그려야 한다.
 */
public record TelemetryPointResponse(
        Instant time,
        Double cpu,
        Double gpu,
        Double memory,
        Double jetsonTemp,
        Double battery) {
}
