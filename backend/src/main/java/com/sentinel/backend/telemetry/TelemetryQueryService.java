package com.sentinel.backend.telemetry;

import java.time.Instant;
import java.util.List;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;

import com.sentinel.backend.telemetry.dto.TelemetryLatestResponse;

/**
 * 최신 센서 값 조회 (S15P11A301-255).
 *
 * <p>mission_id 로 필터하지 않는다 — 임무 밖 telemetry 는 mission_id 가 NULL 이라
 * 임무 단위 API 로는 절대 안 잡힌다(V2 가 NOT NULL 을 푼 이유). 하이퍼테이블은
 * time 인덱스가 자동 생성되므로 ORDER BY time DESC LIMIT 1 은 빠르다.
 */
@Service
public class TelemetryQueryService {

    private final JdbcTemplate jdbc;

    public TelemetryQueryService(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    public TelemetryLatestResponse findLatest() {
        record Env(Instant time, Double temperature, Double humidity) {
        }
        record Mcu(Instant time, Boolean connected) {
        }

        List<Env> env = jdbc.query("""
                        SELECT time, temperature, humidity
                        FROM environment_metrics ORDER BY time DESC LIMIT 1
                        """,
                (rs, i) -> new Env(rs.getTimestamp("time").toInstant(),
                        rs.getObject("temperature", Double.class),
                        rs.getObject("humidity", Double.class)));

        List<Mcu> mcu = jdbc.query("""
                        SELECT time, mcu_connected
                        FROM robot_metrics ORDER BY time DESC LIMIT 1
                        """,
                (rs, i) -> new Mcu(rs.getTimestamp("time").toInstant(),
                        rs.getObject("mcu_connected", Boolean.class)));

        // 주행 지표 (S15P11A301-300). robot_pose 는 다른 하이퍼테이블이라 조회가 하나 는다.
        // time 인덱스가 자동 생성되므로 LIMIT 1 은 싸다.
        record Motion(Instant time, Double linear, Double angular) {
        }
        List<Motion> motion = jdbc.query("""
                        SELECT time, linear_velocity, angular_velocity
                        FROM robot_pose ORDER BY time DESC LIMIT 1
                        """,
                (rs, i) -> new Motion(rs.getTimestamp("time").toInstant(),
                        rs.getObject("linear_velocity", Double.class),
                        rs.getObject("angular_velocity", Double.class)));

        Env e = env.isEmpty() ? null : env.getFirst();
        Mcu m = mcu.isEmpty() ? null : mcu.getFirst();
        Motion p = motion.isEmpty() ? null : motion.getFirst();
        return new TelemetryLatestResponse(
                e == null ? null : e.time(),
                e == null ? null : e.temperature(),
                e == null ? null : e.humidity(),
                m == null ? null : m.time(),
                m == null ? null : m.connected(),
                p == null ? null : p.time(),
                p == null ? null : p.linear(),
                p == null ? null : p.angular());
    }
}
