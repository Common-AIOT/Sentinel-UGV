package com.sentinel.backend.messaging.dto;

/**
 * ROBOT_TELEMETRY 본문 (명세 31-6, {@code common/schemas/telemetry.schema.json}).
 *
 * <p>계약상 7개 키는 항상 존재하지만 값은 null 일 수 있다. ESP32 연동(S15P11A301-84~86) 전에는
 * {@code motion}·{@code battery}·{@code environment}·{@code health.mcuConnected} 가 null 이고,
 * SLAM 이 붙기 전에는 {@code pose} 가 null 이다. 즉 null 은 오류가 아니라 정상 입력이다.
 *
 * <p>{@code health} 의 boolean 은 Boolean 으로 받는다. false(확인했고 끊김)와 null(확인 수단 없음)을
 * 구분해야 관제 화면이 "장애"와 "미구현"을 다르게 표시할 수 있다.
 */
public record TelemetryData(
        Pose pose,
        Motion motion,
        Battery battery,
        Environment environment,
        Compute compute,
        Health health,
        String missionState
) {
    public record Pose(Double x, Double y, Double yaw, String mapId) {
    }

    public record Motion(Double linearVelocityMps, Double angularVelocityRadps) {
    }

    public record Battery(Double voltage, Double percent) {
    }

    public record Environment(Double temperatureC, Double humidityPercent) {
    }

    public record Compute(Double cpuPercent, Double gpuPercent, Double memoryPercent, Double jetsonTempC) {
    }

    /**
     * 구성요소별 연결 상태.
     *
     * <p>{@code recorderOk}·{@code recorderLastFailure} 는 S15P11A301-310 에서 더했다.
     * 계약상 **필수가 아니다** — recorder 없이 도는 구성에서는 키 자체가 없다. Jackson 이
     * 없는 키를 null 로 채우므로 「키 없음」과 「null」이 자연히 같게 처리된다.
     *
     * <p>둘은 독립이다. {@code recorderOk=true} 와 {@code recorderLastFailure} 가 함께 오는
     * 것이 정상 조합이며 「지금은 정상이지만 이번 기동에 실패가 있었다」는 뜻이다 — 젯슨이
     * 성공해도 사유를 지우지 않는다. 합치면 간헐 실패가 성공에 덮여 재발을 못 잡는다.
     *
     * <p>{@code recorderOk} 의 null 은 「정상」이 아니라 「이번 기동에서 마감한 이벤트가
     * 없어 판정할 근거가 없음」이다. mcuConnected 의 3값 원칙과 같다.
     *
     * <p>{@code recorderLastFailure} 는 String 이다. 젯슨이 {@code RECORDING_FAILED_{사유}}
     * 로 만들어 값이 늘어나므로 enum 으로 고정하지 않는다.
     *
     * <p>{@code motorLinkOk} 는 S15P11A301-317 에서 더했다. {@code mcuConnected} 와
     * <b>다른 보드</b>다 — 그쪽은 엔코더를 내는 센서 ESP32 이고 이 값은 바퀴를 돌리는
     * 모터 ESP32 다. 보드가 둘인데 값이 하나뿐이라, 2026-08-06 실기동에서 모터 보드만
     * 죽었을 때 관제 화면이 그것을 말할 방법이 없었다. 이것도 필수가 아니다(옛 젯슨은
     * 키를 보내지 않는다).
     */
    public record Health(
            Boolean mcuConnected,
            Boolean lidarOk,
            Boolean cameraOk,
            Boolean motorLinkOk,
            Boolean recorderOk,
            String recorderLastFailure) {
    }
}
