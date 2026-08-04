package com.sentinel.backend.control;

import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.List;
import java.util.UUID;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;

import com.sentinel.backend.common.exception.BusinessException;
import com.sentinel.backend.common.exception.ErrorCode;
import com.sentinel.backend.control.dto.CommandResponse;
import com.sentinel.backend.control.dto.CommandStatusResponse;
import com.sentinel.backend.messaging.MqttGateway;
import com.sentinel.backend.messaging.dto.MessageEnvelope;
import com.sentinel.backend.messaging.dto.MissionCommandData;

import tools.jackson.databind.ObjectMapper;

/**
 * 임무 제어 명령 발행 (명세 27.4 {@code POST /missions/{id}/commands}).
 *
 * <p>202 는 로봇이 동작을 완료했다는 뜻이 아니다. commandId 와 PENDING 을 돌려주고,
 * 실제 수락·거부는 젯슨의 COMMAND_ACK 가 {@code control_commands.result} 를 갱신한다.
 *
 * <p>발행 실패는 즉시 503 으로 알린다. 명령은 지연 실행보다 명확한 실패가 안전하다.
 * 실패한 명령은 FAILED 로 남겨 PENDING 과 구분한다.
 */
@Service
public class MissionCommandService {

    private final JdbcTemplate jdbc;
    private final MqttGateway gateway;
    private final ObjectMapper objectMapper;

    public MissionCommandService(JdbcTemplate jdbc, MqttGateway gateway, ObjectMapper objectMapper) {
        this.jdbc = jdbc;
        this.gateway = gateway;
        this.objectMapper = objectMapper;
    }

    public CommandResponse issue(UUID missionId, String type) {
        List<MissionRow> rows = jdbc.query("""
                        SELECT r.name AS robot_name, m.ended_at IS NOT NULL AS ended
                        FROM missions m JOIN robots r ON r.id = m.robot_id
                        WHERE m.id = ?
                        """,
                (rs, i) -> new MissionRow(rs.getString("robot_name"), rs.getBoolean("ended")),
                missionId);
        if (rows.isEmpty()) {
            throw new BusinessException(ErrorCode.MISSION_NOT_FOUND);
        }
        MissionRow mission = rows.getFirst();
        if (mission.ended()) {
            throw new BusinessException(ErrorCode.MISSION_ALREADY_ENDED);
        }

        UUID commandId = UUID.randomUUID();
        jdbc.update("""
                        INSERT INTO control_commands (mission_id, command_id, type, requested_at, result)
                        VALUES (?, ?, ?, now(), 'PENDING')
                        """,
                missionId, commandId, type);

        // 봉투 스키마의 sentAt 패턴이 소수점 이하 6자리까지만 허용한다. Jackson 은
        // Instant 를 나노초(9자리)로 직렬화하므로 밀리초로 절단해 계약을 지킨다.
        Instant now = Instant.now().truncatedTo(ChronoUnit.MILLIS);
        MessageEnvelope envelope = new MessageEnvelope(
                MessageEnvelope.SCHEMA_VERSION,
                UUID.randomUUID(),
                MessageEnvelope.TYPE_MISSION_COMMAND,
                mission.robotName(),
                missionId,
                now.toEpochMilli(),  // 서버 발행 봉투의 sequence 는 시각 기반 단조 증가 값을 쓴다
                now,
                objectMapper.valueToTree(new MissionCommandData(commandId, type)));
        try {
            gateway.publish(mission.robotName(), "cmd/mission", envelope);
        } catch (BusinessException e) {
            jdbc.update("UPDATE control_commands SET result = 'FAILED' WHERE command_id = ?", commandId);
            throw e;
        }
        return new CommandResponse(commandId, "PENDING");
    }

    /**
     * 임무의 명령 이력 조회 (S15P11A301-207). 최신 요청부터 내려준다.
     *
     * <p>result 는 발급 시 PENDING 으로 시작해 젯슨 ACK 가 갱신한다(27.6). 과거에
     * result 없이 적재된 행이 있어도 PENDING 으로 노출한다 — 화면이 null 을 해석하게
     * 두지 않는다.
     */
    public List<CommandStatusResponse> findCommands(UUID missionId) {
        Boolean exists = jdbc.queryForObject(
                "SELECT EXISTS(SELECT 1 FROM missions WHERE id = ?)", Boolean.class, missionId);
        if (!Boolean.TRUE.equals(exists)) {
            throw new BusinessException(ErrorCode.MISSION_NOT_FOUND);
        }
        return jdbc.query("""
                        SELECT command_id, type, result, reason_code, requested_at
                        FROM control_commands
                        WHERE mission_id = ?
                        ORDER BY requested_at DESC
                        """,
                (rs, i) -> new CommandStatusResponse(
                        rs.getObject("command_id", UUID.class),
                        rs.getString("type"),
                        rs.getString("result") == null ? "PENDING" : rs.getString("result"),
                        rs.getString("reason_code"),
                        rs.getTimestamp("requested_at").toInstant()),
                missionId);
    }

    private record MissionRow(String robotName, boolean ended) {
    }
}
