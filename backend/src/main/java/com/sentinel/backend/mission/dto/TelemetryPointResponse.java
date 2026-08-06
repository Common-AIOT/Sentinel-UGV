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
        Boolean mcuConnected,
        /**
         * 녹화기 상태 (S15P11A301-310). 구간 bool_and — 한 번이라도 실패했으면 false,
         * 보고가 없었으면 null(판정 근거 없음이며 「정상」이 아니다).
         */
        Boolean recorderOk,
        /**
         * 마지막 마감 실패 사유. {@code recorderOk=true} 와 함께 오는 것이 정상이며
         * 「지금은 정상이지만 이번 기동에 실패가 있었다」는 뜻이다 — 젯슨이 성공해도
         * 지우지 않는다. 값이 늘어날 수 있어 문자열 그대로 내보낸다.
         */
        String recorderLastFailure) {
}
