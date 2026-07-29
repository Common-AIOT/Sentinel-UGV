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

    public record Health(Boolean mcuConnected, Boolean lidarOk, Boolean cameraOk) {
    }
}
