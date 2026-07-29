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
            INSERT INTO robot_metrics (time, mission_id, battery, voltage, cpu, gpu, memory, jetson_temp, state)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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

        TelemetryData.Pose pose = data.pose();
        if (pose != null) {
            TelemetryData.Motion motion = data.motion();
            jdbc.update(INSERT_POSE,
                    time, missionId, pose.x(), pose.y(), pose.yaw(),
                    motion == null ? null : motion.linearVelocityMps(),
                    motion == null ? null : motion.angularVelocityRadps());
        }

        TelemetryData.Compute compute = data.compute();
        TelemetryData.Battery battery = data.battery();
        if (compute != null || battery != null || data.missionState() != null) {
            jdbc.update(INSERT_METRICS,
                    time, missionId,
                    battery == null ? null : battery.percent(),
                    battery == null ? null : battery.voltage(),
                    compute == null ? null : compute.cpuPercent(),
                    compute == null ? null : compute.gpuPercent(),
                    compute == null ? null : compute.memoryPercent(),
                    compute == null ? null : compute.jetsonTempC(),
                    data.missionState());
        }

        TelemetryData.Environment environment = data.environment();
        if (environment != null) {
            jdbc.update(INSERT_ENVIRONMENT,
                    time, missionId, environment.temperatureC(), environment.humidityPercent());
        }
    }
}
