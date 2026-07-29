package com.sentinel.backend.control;

import java.sql.Timestamp;
import java.time.Duration;
import java.time.Instant;
import java.util.List;
import java.util.UUID;

import org.springframework.dao.DuplicateKeyException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;

import com.sentinel.backend.common.exception.BusinessException;
import com.sentinel.backend.common.exception.ErrorCode;
import com.sentinel.backend.control.dto.ControlSessionResponse;

/**
 * 제어권 세션 (명세 11.4). 로봇 한 대에 유효 세션은 1개다.
 *
 * <p>로봇당 1행은 {@code uq_control_sessions_robot} 유니크 인덱스가 DB 레벨에서 보장한다.
 * 두 운영자가 동시에 요청해도 한쪽만 성공한다.
 *
 * <p>WebSocket heartbeat 기반 자동 회수(11.4)는 조이스틱 연동(S15P11A301-39)에서 다룬다.
 * 여기서는 TTL 만료 세션을 새 발급 시점에 청소하는 것까지만 한다.
 */
@Service
public class ControlSessionService {

    private static final Duration SESSION_TTL = Duration.ofMinutes(10);

    private final JdbcTemplate jdbc;

    public ControlSessionService(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    public ControlSessionResponse issue(String robotName) {
        List<UUID> robotUuids = jdbc.query(
                "SELECT id FROM robots WHERE name = ?",
                (rs, i) -> rs.getObject("id", UUID.class), robotName);
        if (robotUuids.isEmpty()) {
            throw new BusinessException(ErrorCode.ROBOT_NOT_FOUND);
        }

        // 만료된 세션은 자리만 차지한다. 발급 전에 치워 유니크 인덱스와 충돌하지 않게 한다.
        jdbc.update("DELETE FROM control_sessions WHERE robot_id = ? AND expires_at < now()",
                robotUuids.getFirst());

        UUID sessionId = UUID.randomUUID();
        Instant expiresAt = Instant.now().plus(SESSION_TTL);
        try {
            jdbc.update("INSERT INTO control_sessions (session_id, robot_id, expires_at) VALUES (?, ?, ?)",
                    sessionId, robotUuids.getFirst(), Timestamp.from(expiresAt));
        } catch (DuplicateKeyException e) {
            throw new BusinessException(ErrorCode.CONTROL_SESSION_DENIED);
        }
        return new ControlSessionResponse(sessionId, robotName, expiresAt);
    }

    public void release(UUID sessionId) {
        int deleted = jdbc.update("DELETE FROM control_sessions WHERE session_id = ?", sessionId);
        if (deleted == 0) {
            throw new BusinessException(ErrorCode.CONTROL_SESSION_NOT_FOUND);
        }
    }
}
