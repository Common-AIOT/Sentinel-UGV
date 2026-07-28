package com.example.backend.robot;

import java.sql.Timestamp;
import java.time.Instant;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;

import com.example.backend.messaging.dto.MessageEnvelope;
import com.example.backend.messaging.dto.PresenceData;

/**
 * 로봇 접속 상태를 {@code robots} 에 반영한다 (명세 31-4).
 *
 * <p>시각은 봉투의 {@code sentAt} 이 아니라 서버 수신 시각을 쓴다. Last Will 은 브로커가 접속
 * 시점의 메시지를 나중에 발행하는 구조라 {@code sentAt} 이 실제 단절 시각이 아니다.
 *
 * <p>이 신호는 관제 표시용이며 모터 정지 근거로 쓰지 않는다. 모터는 젯슨·ESP32 의 로컬
 * watchdog 이 훨씬 빠르게 세운다.
 */
@Service
public class RobotPresenceWriter {

    private static final String UPSERT = """
            INSERT INTO robots (name, status, last_seen_at)
            VALUES (?, ?, ?)
            ON CONFLICT (name) DO UPDATE
            SET status = EXCLUDED.status, last_seen_at = EXCLUDED.last_seen_at
            """;

    private final JdbcTemplate jdbc;

    public RobotPresenceWriter(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    public void write(MessageEnvelope envelope, PresenceData data) {
        Timestamp receivedAt = Timestamp.from(Instant.now());
        jdbc.update(UPSERT, envelope.robotId(), data.status(), receivedAt);
    }
}
