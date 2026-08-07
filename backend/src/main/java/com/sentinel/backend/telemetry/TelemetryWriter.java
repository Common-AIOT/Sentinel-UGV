package com.sentinel.backend.telemetry;

import java.sql.Timestamp;
import java.util.UUID;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;

import com.sentinel.backend.messaging.dto.MessageEnvelope;
import com.sentinel.backend.messaging.dto.TelemetryData;

/**
 * telemetry 를 TimescaleDB hypertable 에 기록한다 (명세 13.3).
 *
 * <p>JPA 대신 JdbcTemplate 을 쓴다. hypertable 은 시간 기준 append-only 이고 엔티티 식별자가
 * 없어 JPA 매핑이 이득이 없다.
 *
 * <p>본문이 null 인 그룹은 삽입을 건너뛴다. ESP32 연동 전에는 motion·battery·environment 가
 * null 로 오므로, 빈 행을 만들면 그래프에 의미 없는 0 이 남는다.
 */
@Service
public class TelemetryWriter {

    private static final String INSERT_POSE = """
            INSERT INTO robot_pose (time, mission_id, x, y, yaw, linear_velocity, angular_velocity)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """;

    private static final String INSERT_METRICS = """
            INSERT INTO robot_metrics (time, mission_id, battery, voltage, cpu, gpu, memory, jetson_temp, state,
                                       mcu_connected, motor_link_ok, recorder_ok, recorder_last_failure)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """;

    private static final String INSERT_ENVIRONMENT = """
            INSERT INTO environment_metrics (time, mission_id, temperature, humidity)
            VALUES (?, ?, ?, ?)
            """;

    private final JdbcTemplate jdbc;

    public TelemetryWriter(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    public void write(MessageEnvelope envelope, TelemetryData data) {
        Timestamp time = Timestamp.from(envelope.sentAt());
        UUID missionId = envelope.missionId();

        // pose(SLAM)와 motion(엔코더)은 출처가 다른 독립 그룹이다. SLAM 이 죽어도
        // 엔코더 속도는 기록한다 — 어느 한쪽만 와도 행을 만든다(S15P11A301-205).
        TelemetryData.Pose pose = data.pose();
        TelemetryData.Motion motion = data.motion();
        if (pose != null || motion != null) {
            jdbc.update(INSERT_POSE,
                    time, missionId,
                    pose == null ? null : pose.x(),
                    pose == null ? null : pose.y(),
                    pose == null ? null : pose.yaw(),
                    motion == null ? null : motion.linearVelocityMps(),
                    motion == null ? null : motion.angularVelocityRadps());
        }

        TelemetryData.Compute compute = data.compute();
        TelemetryData.Battery battery = data.battery();
        TelemetryData.Health health = data.health();
        if (compute != null || battery != null || health != null || data.missionState() != null) {
            jdbc.update(INSERT_METRICS,
                    time, missionId,
                    battery == null ? null : battery.percent(),
                    battery == null ? null : battery.voltage(),
                    compute == null ? null : compute.cpuPercent(),
                    compute == null ? null : compute.gpuPercent(),
                    compute == null ? null : compute.memoryPercent(),
                    compute == null ? null : compute.jetsonTempC(),
                    data.missionState(),
                    health == null ? null : health.mcuConnected(),
                    // 모터 보드 링크 (S15P11A301-317). mcuConnected 와 다른 보드다.
                    health == null ? null : health.motorLinkOk(),
                    // 녹화기 상태 (S15P11A301-310). 키가 없으면 Jackson 이 null 로 채우므로
                    // 「필드 없음」과 「null」이 같게 저장된다 — 젯슨 재빌드 전 스택이 보내는
                    // 옛 형식(두 필드 없음)이 그대로 통과한다.
                    health == null ? null : health.recorderOk(),
                    health == null ? null : health.recorderLastFailure());
        }

        TelemetryData.Environment environment = data.environment();
        if (environment != null) {
            jdbc.update(INSERT_ENVIRONMENT,
                    time, missionId, environment.temperatureC(), environment.humidityPercent());
        }
    }
}
