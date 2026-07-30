package com.sentinel.backend.encounter;

import java.sql.Timestamp;
import java.time.Instant;
import java.util.UUID;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;

import com.sentinel.backend.messaging.dto.InteractionReportData;
import com.sentinel.backend.messaging.dto.MessageEnvelope;

/**
 * 음성 상호작용 보고를 이벤트 원문과 encounter 요약에 저장한다 (S15P11A301-159).
 *
 * <p>전체 구조화 결과는 {@code events.payload_json}에 보존하고, 기존 encounter 조회
 * API가 바로 활용할 수 있는 응답 인원·요약·종료 사유는 {@code encounters}에 반영한다.
 * message_id UNIQUE로 MQTT QoS 1 재전송을 멱등 처리한다.
 */
@Service
public class InteractionReportWriter {

    private static final Logger log = LoggerFactory.getLogger(InteractionReportWriter.class);

    private static final String INSERT_EVENT = """
            INSERT INTO events (mission_id, message_id, type, severity, occurred_at, payload_json)
            VALUES (?, ?, 'VOICE_INTERACTION_REPORT', ?, ?, CAST(? AS jsonb))
            ON CONFLICT (message_id) DO NOTHING
            """;

    private static final String UPDATE_ENCOUNTER = """
            UPDATE encounters
               SET responsive_person_count = ?,
                   interaction_summary = ?,
                   termination_reason = ?
             WHERE id = ?
            """;

    private final JdbcTemplate jdbc;

    public InteractionReportWriter(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    public void write(MessageEnvelope envelope, InteractionReportData data) {
        UUID missionId = envelope.missionId() != null
                ? envelope.missionId() : data.missionId();
        if (missionId == null) {
            log.warn("missionId 없는 음성 보고는 적재하지 않는다 (interactionId={})",
                    data.interactionId());
            return;
        }

        Boolean missionExists = jdbc.queryForObject(
                "SELECT EXISTS(SELECT 1 FROM missions WHERE id = ?)",
                Boolean.class,
                missionId);
        if (!Boolean.TRUE.equals(missionExists)) {
            log.warn("서버가 모르는 임무의 음성 보고는 적재하지 않는다 "
                            + "(interactionId={}, missionId={})",
                    data.interactionId(), missionId);
            return;
        }

        InteractionReportData.RiskAssessment risk = data.riskAssessment();
        InteractionReportData.SessionReport report = data.sessionReport();
        Instant occurredAt = data.endedAt() != null ? data.endedAt() : envelope.sentAt();
        jdbc.update(
                INSERT_EVENT,
                missionId,
                envelope.messageId(),
                risk.riskLevel(),
                Timestamp.from(occurredAt != null ? occurredAt : Instant.now()),
                envelope.data().toString());

        int updated = jdbc.update(
                UPDATE_ENCOUNTER,
                report.reportedResponsiveCount(),
                summary(data),
                report.terminationReason(),
                data.encounterId());
        if (updated == 0) {
            log.warn("encounter 없이 온 음성 보고 (encounterId={}, interactionId={})",
                    data.encounterId(), data.interactionId());
        }
    }

    private String summary(InteractionReportData data) {
        InteractionReportData.SessionReport report = data.sessionReport();
        return "riskLevel=" + data.riskAssessment().riskLevel()
                + "; mobilityStatus=" + report.mobilityStatus()
                + "; urgentConditionReported=" + report.urgentConditionReported()
                + "; usedFallback=" + data.usedFallback();
    }
}
