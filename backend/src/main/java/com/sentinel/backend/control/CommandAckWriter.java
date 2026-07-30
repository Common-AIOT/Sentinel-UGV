package com.sentinel.backend.control;

import java.sql.Timestamp;
import java.time.Instant;
import java.util.List;
import java.util.UUID;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;

import com.sentinel.backend.messaging.dto.CommandAckData;
import com.sentinel.backend.messaging.dto.MessageEnvelope;
import com.sentinel.backend.messaging.dto.MissionCommandData;

/**
 * COMMAND_ACK 를 {@code control_commands.result} 에 반영한다 (명세 27.6 "명령 요청부터
 * ACK 까지 상태가 추적된다").
 *
 * <p>수락(ACCEPTED/EXECUTED)이면 임무 상태도 전이한다. 서버가 임의로 상태를 만드는 게
 * 아니라 로봇의 회신을 근거로 반영하는 것이다(27.3). 같은 ACK 가 QoS 1 로 두 번 와도
 * UPDATE 값이 결정적이라 멱등이다.
 */
@Service
public class CommandAckWriter {

    private static final Logger log = LoggerFactory.getLogger(CommandAckWriter.class);

    /**
     * 임무 종료 시점의 결과 집계 (S15P11A301-166). 임무 목록·상세의
     * durationSec·distanceM·detectionCount 가 이 행에서 나온다.
     *
     * <p>distance_m 은 robot_pose 인접 좌표 거리의 합, coverage 는 산출 근거가 없어 null.
     * QoS 1 중복 ACK 에도 PK(mission_id) DO NOTHING 으로 행은 1개고, ended_at 이
     * 첫 ACK 에서 고정되므로 값도 결정적이다.
     */
    private static final String INSERT_RESULTS = """
            INSERT INTO mission_results (mission_id, duration_sec, distance_m, coverage, detection_count)
            SELECT m.id,
                   CAST(EXTRACT(EPOCH FROM (m.ended_at - m.started_at)) AS INTEGER),
                   (SELECT sum(step) FROM (
                        SELECT sqrt(power(x - lag(x) OVER (ORDER BY time), 2)
                                  + power(y - lag(y) OVER (ORDER BY time), 2)) AS step
                        FROM robot_pose WHERE mission_id = ?
                    ) steps),
                   NULL,
                   (SELECT count(*) FROM encounters WHERE mission_id = ?)
            FROM missions m
            WHERE m.id = ?
            ON CONFLICT (mission_id) DO NOTHING
            """;

    private final JdbcTemplate jdbc;

    public CommandAckWriter(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    public void write(MessageEnvelope envelope, CommandAckData data) {
        int updated = jdbc.update(
                "UPDATE control_commands SET result = ? WHERE command_id = ?",
                data.status(), data.commandId());
        if (updated == 0) {
            log.warn("모르는 명령의 ACK: commandId={}, status={}", data.commandId(), data.status());
            return;
        }
        if (!CommandAckData.STATUS_ACCEPTED.equals(data.status())
                && !CommandAckData.STATUS_EXECUTED.equals(data.status())) {
            log.info("명령 거부·실패 ACK: commandId={}, status={}, reasonCode={}",
                    data.commandId(), data.status(), data.reasonCode());
            return;
        }

        List<CommandRow> rows = jdbc.query(
                "SELECT mission_id, type FROM control_commands WHERE command_id = ?",
                (rs, i) -> new CommandRow(rs.getObject("mission_id", UUID.class), rs.getString("type")),
                data.commandId());
        CommandRow command = rows.getFirst();
        if (command.missionId() == null) {
            return;
        }
        applyMissionTransition(command, envelope.sentAt() != null ? envelope.sentAt() : Instant.now());
    }

    private void applyMissionTransition(CommandRow command, Instant ackAt) {
        Timestamp at = Timestamp.from(ackAt);
        switch (command.type()) {
            case MissionCommandData.TYPE_START -> jdbc.update(
                    "UPDATE missions SET status = 'EXPLORING', started_at = COALESCE(started_at, ?) WHERE id = ?",
                    at, command.missionId());
            case MissionCommandData.TYPE_PAUSE -> jdbc.update(
                    "UPDATE missions SET status = 'PAUSED' WHERE id = ?", command.missionId());
            case MissionCommandData.TYPE_RESUME -> jdbc.update(
                    "UPDATE missions SET status = 'EXPLORING' WHERE id = ?", command.missionId());
            case MissionCommandData.TYPE_RETURN -> jdbc.update(
                    "UPDATE missions SET status = 'RETURNING' WHERE id = ?", command.missionId());
            case MissionCommandData.TYPE_STOP -> {
                jdbc.update(
                        """
                        UPDATE missions SET status = 'COMPLETED', ended_at = COALESCE(ended_at, ?),
                               end_reason = COALESCE(end_reason, 'OPERATOR_STOP') WHERE id = ?
                        """,
                        at, command.missionId());
                jdbc.update(INSERT_RESULTS,
                        command.missionId(), command.missionId(), command.missionId());
            }
            default -> log.warn("알 수 없는 명령 type 의 ACK: {}", command.type());
        }
    }

    private record CommandRow(UUID missionId, String type) {
    }
}
