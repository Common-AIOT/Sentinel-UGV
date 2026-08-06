package com.sentinel.backend.encounter;

import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Timestamp;
import java.time.Instant;
import java.util.List;
import java.util.UUID;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;

import com.sentinel.backend.common.exception.BusinessException;
import com.sentinel.backend.common.exception.ErrorCode;
import com.sentinel.backend.encounter.dto.EncounterDetailResponse;
import com.sentinel.backend.encounter.dto.EncounterMediaResponse;
import com.sentinel.backend.encounter.dto.EncounterSummaryResponse;

import tools.jackson.core.type.TypeReference;
import tools.jackson.databind.ObjectMapper;

/**
 * 발견(encounter) 목록·상세 조회 (명세 27.4·31-7).
 *
 * <p>적재는 {@link EncounterWriter}, 조회는 여기. MissionService 와 같은 이유로
 * JdbcTemplate 을 쓴다. 상태 전이 세부·detections·interaction_* 연계는 MVP 범위 밖이라
 * 저장된 행을 그대로 내려준다.
 */
@Service
public class EncounterQueryService {

    private static final String SELECT_BY_MISSION = """
            SELECT id, status, map_x, map_y, map_yaw, detected_person_count,
                   started_at, ended_at, termination_reason
            FROM encounters
            WHERE mission_id = ?
            ORDER BY started_at DESC
            """;

    private static final String SELECT_DETAIL = """
            SELECT id, mission_id, status, map_x, map_y, map_yaw,
                   detected_person_count, responsive_person_count, unresponsive_person_count,
                   interaction_summary, voice_encounter_pose, additional_person_reports,
                   started_at, interaction_started_at,
                   interaction_ended_at, ended_at, termination_reason
            FROM encounters
            WHERE id = ?
            """;

    private static final String SELECT_MEDIA = """
            SELECT id, type, storage_status, duration_ms
            FROM media_assets
            WHERE encounter_id = ?
            ORDER BY created_at
            """;

    private final JdbcTemplate jdbc;
    private final ObjectMapper objectMapper;

    public EncounterQueryService(JdbcTemplate jdbc, ObjectMapper objectMapper) {
        this.jdbc = jdbc;
        this.objectMapper = objectMapper;
    }

    /** 임무별 발견 목록 (최신순). 임무가 없으면 404, 발견이 없으면 빈 목록이다. */
    public List<EncounterSummaryResponse> findByMission(UUID missionId) {
        Boolean missionExists = jdbc.queryForObject(
                "SELECT EXISTS(SELECT 1 FROM missions WHERE id = ?)", Boolean.class, missionId);
        if (!Boolean.TRUE.equals(missionExists)) {
            throw new BusinessException(ErrorCode.MISSION_NOT_FOUND);
        }
        return jdbc.query(SELECT_BY_MISSION, (rs, i) -> new EncounterSummaryResponse(
                rs.getObject("id", UUID.class),
                rs.getString("status"),
                rs.getObject("map_x", Double.class),
                rs.getObject("map_y", Double.class),
                rs.getObject("map_yaw", Double.class),
                rs.getObject("detected_person_count", Integer.class),
                toInstant(rs.getTimestamp("started_at")),
                toInstant(rs.getTimestamp("ended_at")),
                rs.getString("termination_reason")), missionId);
    }

    /** 발견 상세와 연결된 미디어 목록. */
    public EncounterDetailResponse findDetail(UUID encounterId) {
        List<EncounterDetailResponse> found = jdbc.query(SELECT_DETAIL,
                (rs, i) -> mapDetail(rs), encounterId);
        if (found.isEmpty()) {
            throw new BusinessException(ErrorCode.ENCOUNTER_NOT_FOUND);
        }
        return found.getFirst();
    }

    private EncounterDetailResponse mapDetail(ResultSet rs) throws SQLException {
        UUID encounterId = rs.getObject("id", UUID.class);
        List<EncounterMediaResponse> media = jdbc.query(SELECT_MEDIA,
                (mrs, i) -> new EncounterMediaResponse(
                        mrs.getObject("id", UUID.class),
                        mrs.getString("type"),
                        mrs.getString("storage_status"),
                        mrs.getObject("duration_ms", Long.class)), encounterId);
        return new EncounterDetailResponse(
                encounterId,
                rs.getObject("mission_id", UUID.class),
                rs.getString("status"),
                rs.getObject("map_x", Double.class),
                rs.getObject("map_y", Double.class),
                rs.getObject("map_yaw", Double.class),
                rs.getObject("detected_person_count", Integer.class),
                rs.getObject("responsive_person_count", Integer.class),
                rs.getObject("unresponsive_person_count", Integer.class),
                rs.getString("interaction_summary"),
                readPose(rs.getString("voice_encounter_pose")),
                readAdditionalPersonReports(
                        rs.getString("additional_person_reports")),
                toInstant(rs.getTimestamp("started_at")),
                toInstant(rs.getTimestamp("interaction_started_at")),
                toInstant(rs.getTimestamp("interaction_ended_at")),
                toInstant(rs.getTimestamp("ended_at")),
                rs.getString("termination_reason"),
                media);
    }

    private EncounterDetailResponse.EncounterPose readPose(String json)
            throws SQLException {
        if (json == null || json.isBlank()) {
            return null;
        }
        try {
            return objectMapper.readValue(
                    json, EncounterDetailResponse.EncounterPose.class);
        } catch (RuntimeException error) {
            throw new SQLException("voice_encounter_pose JSON 해석 실패", error);
        }
    }

    private List<EncounterDetailResponse.AdditionalPersonReport>
            readAdditionalPersonReports(String json) throws SQLException {
        if (json == null || json.isBlank()) {
            return List.of();
        }
        try {
            return objectMapper.readValue(
                    json,
                    new TypeReference<List<
                            EncounterDetailResponse.AdditionalPersonReport>>() {});
        } catch (RuntimeException error) {
            throw new SQLException(
                    "additional_person_reports JSON 해석 실패", error);
        }
    }

    private Instant toInstant(Timestamp timestamp) {
        return timestamp == null ? null : timestamp.toInstant();
    }
}
