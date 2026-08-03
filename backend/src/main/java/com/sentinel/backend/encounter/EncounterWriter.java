package com.sentinel.backend.encounter;

import java.sql.Timestamp;
import java.time.Instant;
import java.util.UUID;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;

import com.sentinel.backend.messaging.dto.EncounterData;
import com.sentinel.backend.messaging.dto.MessageEnvelope;
import com.sentinel.backend.realtime.EncounterChangedMessage;
import com.sentinel.backend.realtime.RealtimeBroadcaster;

/**
 * ENCOUNTER_CONFIRMED 를 {@code encounters} 에 적재한다 (명세 13.2·29.3).
 *
 * <p>31-7 미디어 업로드의 선행 데이터다. {@code POST /media/uploads} 가 encounterId 로
 * mission_id 를 알아내려면 이 행이 먼저 있어야 한다.
 *
 * <p>멱등성: id 가 젯슨 생성 UUID PK 라 QoS 1 중복 수신에도 행은 1개다(29.3).
 * CONFIRMED 재전송이 뒤늦게 와도 진행된 상태를 되돌리지 않도록 DO NOTHING 으로 둔다.
 * 상태 전이 세부와 interaction_*·응답자 수 연계는 MVP 범위 밖이라 phase 를 status 로
 * 그대로 저장한다.
 */
@Service
public class EncounterWriter {

    private static final Logger log = LoggerFactory.getLogger(EncounterWriter.class);

    private static final String INSERT_CONFIRMED = """
            INSERT INTO encounters (id, mission_id, status, map_x, map_y, map_yaw,
                                    detected_person_count, started_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (id) DO NOTHING
            """;

    private static final String UPDATE_APPROACHED = """
            UPDATE encounters SET status = ?, interaction_started_at = ? WHERE id = ?
            """;

    private static final String UPDATE_ENDED = """
            UPDATE encounters SET status = ?, interaction_ended_at = ?, ended_at = ? WHERE id = ?
            """;

    // 사후 3초 안에 재감지되어 이벤트가 재개된 것이므로 종료 시각을 되돌린다(32-5).
    private static final String UPDATE_REDETECTED = """
            UPDATE encounters SET status = ?, interaction_ended_at = NULL, ended_at = NULL WHERE id = ?
            """;

    private static final String UPDATE_LOST = """
            UPDATE encounters SET status = ?, ended_at = ?, termination_reason = 'PERSON_LOST' WHERE id = ?
            """;

    private final JdbcTemplate jdbc;
    private final RealtimeBroadcaster broadcaster;

    public EncounterWriter(JdbcTemplate jdbc, RealtimeBroadcaster broadcaster) {
        this.jdbc = jdbc;
        this.broadcaster = broadcaster;
    }

    public void write(MessageEnvelope envelope, EncounterData data) {
        UUID encounterId = data.encounterId();
        Timestamp detectedAt = Timestamp.from(
                data.detectedAt() != null ? data.detectedAt() : Instant.now());

        boolean applied = switch (data.phase()) {
            case EncounterData.PHASE_CONFIRMED -> insertConfirmed(envelope, data, detectedAt);
            case EncounterData.PHASE_APPROACHED -> updateOrWarn(data,
                    jdbc.update(UPDATE_APPROACHED, data.phase(), detectedAt, encounterId));
            case EncounterData.PHASE_ENDED -> updateOrWarn(data,
                    jdbc.update(UPDATE_ENDED, data.phase(), detectedAt, detectedAt, encounterId));
            case EncounterData.PHASE_REDETECTED -> updateOrWarn(data,
                    jdbc.update(UPDATE_REDETECTED, data.phase(), encounterId));
            case EncounterData.PHASE_LOST -> updateOrWarn(data,
                    jdbc.update(UPDATE_LOST, data.phase(), detectedAt, encounterId));
            default -> {
                log.warn("알 수 없는 encounter phase: {} (encounterId={})", data.phase(), encounterId);
                yield false;
            }
        };

        // DB 반영 직후 관제로 푸시(31-8). QoS 1 중복으로 반영이 없었으면 알림도 없다.
        UUID missionId = envelope.missionId() != null ? envelope.missionId() : data.missionId();
        if (applied && missionId != null) {
            EncounterData.Pose pose = data.pose();
            broadcaster.encounterChanged(missionId, new EncounterChangedMessage(
                    encounterId, data.phase(), data.personCount(),
                    pose == null ? null : pose.x(),
                    pose == null ? null : pose.y(),
                    detectedAt.toInstant()));
        }
    }

    /**
     * 신규 적재. mission_id 는 봉투 우선, 없으면 본문 값을 쓴다.
     *
     * <p>서버가 모르는 임무(행 없음)나 임무 외 탐지(missionId null)는 적재하지 않는다.
     * {@code encounters.mission_id} 가 NOT NULL FK 이고, 임무 없는 encounter 는 관제
     * 화면에서 붙을 곳이 없다.
     */
    private boolean insertConfirmed(MessageEnvelope envelope, EncounterData data, Timestamp detectedAt) {
        UUID missionId = envelope.missionId() != null ? envelope.missionId() : data.missionId();
        if (missionId == null) {
            log.warn("missionId 없는 encounter 는 적재하지 않는다 (encounterId={})", data.encounterId());
            return false;
        }
        Boolean missionExists = jdbc.queryForObject(
                "SELECT EXISTS(SELECT 1 FROM missions WHERE id = ?)", Boolean.class, missionId);
        if (!Boolean.TRUE.equals(missionExists)) {
            log.warn("서버가 모르는 임무의 encounter 는 적재하지 않는다 (encounterId={}, missionId={})",
                    data.encounterId(), missionId);
            return false;
        }

        EncounterData.Pose pose = data.pose();
        return jdbc.update(INSERT_CONFIRMED,
                data.encounterId(), missionId, data.phase(),
                pose == null ? null : pose.x(),
                pose == null ? null : pose.y(),
                pose == null ? null : pose.yaw(),
                data.personCount(), detectedAt) > 0;
    }

    private boolean updateOrWarn(EncounterData data, int updatedRows) {
        if (updatedRows == 0) {
            log.warn("CONFIRMED 없이 온 encounter phase: {} (encounterId={})",
                    data.phase(), data.encounterId());
        }
        return updatedRows > 0;
    }
}
