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
        // 녹화기 상태를 함께 담는다 (S15P11A301-310). 같은 robot_metrics 최신 행에서
        // 나오므로 조회가 늘지 않는다.
        record Mcu(Instant time, Boolean connected, Boolean motorLinkOk,
                   Boolean recorderOk, String recorderLastFailure) {
        }

        List<Env> env = jdbc.query("""
                        SELECT time, temperature, humidity
                        FROM environment_metrics ORDER BY time DESC LIMIT 1
                        """,
                (rs, i) -> new Env(rs.getTimestamp("time").toInstant(),
                        rs.getObject("temperature", Double.class),
                        rs.getObject("humidity", Double.class)));

        List<Mcu> mcu = jdbc.query("""
                        SELECT time, mcu_connected, motor_link_ok, recorder_ok, recorder_last_failure
                        FROM robot_metrics ORDER BY time DESC LIMIT 1
                        """,
                (rs, i) -> new Mcu(rs.getTimestamp("time").toInstant(),
                        rs.getObject("mcu_connected", Boolean.class),
                        rs.getObject("motor_link_ok", Boolean.class),
                        rs.getObject("recorder_ok", Boolean.class),
                        rs.getString("recorder_last_failure")));

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

        // 제어 모드 (S15P11A301-350). 위 셋과 달리 하이퍼테이블이 아니라 `robots` 에서
        // 온다 — **제어 모드는 임무에도 시계열에도 매이지 않는다.** 임무가 닫힌 뒤에
        // 사람이 폰을 잡는 것이 이 값이 필요한 대표 상황이고, 그때 telemetry 는 아예
        // 쌓이지 않는다(2026-08-08 실기동에서 21:04~21:21 구간이 그랬다).
        //
        // `last_seen_at` 을 신선도로 쓰지 않는다 — 그 칸은 PRESENCE 메시지만 갱신하므로
        // 로봇이 접속만 유지한 채 죽어도 「방금 갱신된 MANUAL」로 보인다. 신선도 판단은
        // 프런트가 mcuTime 으로 한다.
        List<String> mode = jdbc.query(
                "SELECT control_mode FROM robots WHERE control_mode IS NOT NULL "
                        + "ORDER BY last_seen_at DESC NULLS LAST LIMIT 1",
                (rs, i) -> rs.getString("control_mode"));

        Env e = env.isEmpty() ? null : env.getFirst();
        Mcu m = mcu.isEmpty() ? null : mcu.getFirst();
        Motion p = motion.isEmpty() ? null : motion.getFirst();
        return new TelemetryLatestResponse(
                mode.isEmpty() ? null : mode.getFirst(),
                e == null ? null : e.time(),
                e == null ? null : e.temperature(),
                e == null ? null : e.humidity(),
                m == null ? null : m.time(),
                m == null ? null : m.connected(),
                m == null ? null : m.motorLinkOk(),
                m == null ? null : m.recorderOk(),
                m == null ? null : m.recorderLastFailure(),
                p == null ? null : p.time(),
                p == null ? null : p.linear(),
                p == null ? null : p.angular());
    }
}
