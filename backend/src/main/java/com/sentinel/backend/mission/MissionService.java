package com.sentinel.backend.mission;

import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Timestamp;
import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;

import com.sentinel.backend.common.exception.BusinessException;
import com.sentinel.backend.common.exception.ErrorCode;
import com.sentinel.backend.mission.dto.MissionDetailResponse;
import com.sentinel.backend.mission.dto.MissionSummaryResponse;
import com.sentinel.backend.mission.dto.TelemetryPointResponse;
import com.sentinel.backend.mission.dto.TrajectoryPointResponse;
import com.sentinel.backend.mission.dto.TrajectoryResponse;

import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

/**
 * 임무 생성·조회와 시계열 그래프 조회 (명세 27.4).
 *
 * <p>hypertable 과 마찬가지로 JdbcTemplate 을 쓴다. 목록·상세가 mission_results 조인과
 * time_bucket 집계라 SQL 이 곧 로직이고, 엔티티 매핑의 이득이 없다.
 */
@Service
public class MissionService {

    private static final String SELECT_SUMMARY = """
            SELECT m.id, r.name AS robot_name, m.status, m.started_at, m.ended_at,
                   m.end_reason, m.created_at,
                   res.duration_sec, res.distance_m, res.detection_count
            FROM missions m
            JOIN robots r ON r.id = m.robot_id
            LEFT JOIN mission_results res ON res.mission_id = m.id
            ORDER BY m.created_at DESC
            LIMIT 100
            """;

    private static final String SELECT_DETAIL = """
            SELECT m.id, r.name AS robot_name, m.status, m.started_at, m.ended_at,
                   m.end_reason, m.created_at, m.home_pose,
                   res.duration_sec, res.distance_m, res.coverage, res.detection_count
            FROM missions m
            JOIN robots r ON r.id = m.robot_id
            LEFT JOIN mission_results res ON res.mission_id = m.id
            WHERE m.id = ?
            """;

    // x IS NOT NULL: 엔코더 전용 행(속도만, 좌표 null — V5)은 궤적점이 아니다.
    private static final String SELECT_TRAJECTORY = """
            SELECT time, x, y, yaw FROM robot_pose
            WHERE mission_id = ? AND x IS NOT NULL ORDER BY time
            """;

    // 조회 창의 기본값이 임무 창(started_at~ended_at)이므로 양끝을 포함한다.
    //
    // 세 하이퍼테이블을 같은 버킷 격자로 각각 집계한 뒤 FULL OUTER JOIN 으로 합친다
    // (S15P11A301-205). 테이블마다 적재 주기가 달라 어느 한쪽에만 버킷이 있을 수 있고,
    // 그때 다른 쪽 필드는 null(결측)로 나가는 것이 맞다 — null 을 0 으로 채우지 않는다.
    // mcu_connected 는 평균이 없는 boolean 이라 bool_and: 구간 중 한 번이라도 끊겼으면
    // false 로 보여 "끊김"을 숨기지 않고, 값이 아예 없으면 null(모름)이다.
    private static final String SELECT_TELEMETRY = """
            WITH m AS (
                SELECT time_bucket(make_interval(secs => ?), time) AS bucket,
                       avg(cpu) AS cpu, avg(gpu) AS gpu, avg(memory) AS memory,
                       avg(jetson_temp) AS jetson_temp, avg(battery) AS battery,
                       bool_and(mcu_connected) AS mcu_connected
                FROM robot_metrics
                WHERE mission_id = ? AND time >= ? AND time <= ?
                GROUP BY bucket
            ), e AS (
                SELECT time_bucket(make_interval(secs => ?), time) AS bucket,
                       avg(temperature) AS temperature, avg(humidity) AS humidity
                FROM environment_metrics
                WHERE mission_id = ? AND time >= ? AND time <= ?
                GROUP BY bucket
            ), p AS (
                SELECT time_bucket(make_interval(secs => ?), time) AS bucket,
                       avg(linear_velocity) AS linear_velocity,
                       avg(angular_velocity) AS angular_velocity
                FROM robot_pose
                WHERE mission_id = ? AND time >= ? AND time <= ?
                GROUP BY bucket
            )
            SELECT COALESCE(m.bucket, e.bucket, p.bucket) AS bucket,
                   m.cpu, m.gpu, m.memory, m.jetson_temp, m.battery, m.mcu_connected,
                   e.temperature, e.humidity,
                   p.linear_velocity, p.angular_velocity
            FROM m
            FULL OUTER JOIN e ON e.bucket = m.bucket
            FULL OUTER JOIN p ON p.bucket = COALESCE(m.bucket, e.bucket)
            ORDER BY bucket
            """;

    private final JdbcTemplate jdbc;
    private final ObjectMapper objectMapper;

    public MissionService(JdbcTemplate jdbc, ObjectMapper objectMapper) {
        this.jdbc = jdbc;
        this.objectMapper = objectMapper;
    }

    /**
     * 임무를 CREATED 상태로 생성한다. 시작·종료 시각은 제어 명령과 로봇 보고가 채운다.
     *
     * <p>한 로봇에는 활성 임무(ended_at IS NULL)가 하나만 존재한다(명세 27.3).
     */
    public MissionDetailResponse create(String robotId) {
        List<UUID> robotUuids = jdbc.query(
                "SELECT id FROM robots WHERE name = ?",
                (rs, i) -> rs.getObject("id", UUID.class), robotId);
        if (robotUuids.isEmpty()) {
            throw new BusinessException(ErrorCode.ROBOT_NOT_FOUND);
        }

        Boolean active = jdbc.queryForObject(
                "SELECT EXISTS(SELECT 1 FROM missions WHERE robot_id = ? AND ended_at IS NULL)",
                Boolean.class, robotUuids.getFirst());
        if (Boolean.TRUE.equals(active)) {
            throw new BusinessException(ErrorCode.MISSION_ALREADY_ACTIVE);
        }

        UUID missionId = UUID.randomUUID();
        jdbc.update("INSERT INTO missions (id, robot_id, status) VALUES (?, ?, 'CREATED')",
                missionId, robotUuids.getFirst());
        return findDetail(missionId);
    }

    public List<MissionSummaryResponse> findAll() {
        return jdbc.query(SELECT_SUMMARY, (rs, i) -> new MissionSummaryResponse(
                rs.getObject("id", UUID.class),
                rs.getString("robot_name"),
                rs.getString("status"),
                toInstant(rs.getTimestamp("started_at")),
                toInstant(rs.getTimestamp("ended_at")),
                rs.getString("end_reason"),
                toInstant(rs.getTimestamp("created_at")),
                rs.getObject("duration_sec", Integer.class),
                rs.getObject("distance_m", Double.class),
                rs.getObject("detection_count", Integer.class)));
    }

    public MissionDetailResponse findDetail(UUID missionId) {
        List<MissionDetailResponse> found = jdbc.query(SELECT_DETAIL,
                (rs, i) -> mapDetail(rs), missionId);
        if (found.isEmpty()) {
            throw new BusinessException(ErrorCode.MISSION_NOT_FOUND);
        }
        return found.getFirst();
    }

    /**
     * robot_metrics 를 bucketSeconds 간격으로 구간 평균해 반환한다.
     *
     * <p>조회 창의 기본값은 임무 창이다: from 은 started_at(없으면 created_at),
     * to 는 ended_at(없으면 현재). 진행 중 임무를 폴링하면 마지막 버킷이 계속 자란다.
     */
    public List<TelemetryPointResponse> findTelemetry(
            UUID missionId, Instant from, Instant to, int bucketSeconds) {
        if (bucketSeconds < 1) {
            throw new IllegalArgumentException("bucketSeconds는 1 이상이어야 합니다.");
        }
        MissionDetailResponse mission = findDetail(missionId);

        Instant effectiveFrom = from != null ? from
                : mission.startedAt() != null ? mission.startedAt() : mission.createdAt();
        Instant effectiveTo = to != null ? to
                : mission.endedAt() != null ? mission.endedAt() : Instant.now();

        Timestamp fromTs = Timestamp.from(effectiveFrom);
        Timestamp toTs = Timestamp.from(effectiveTo);
        return jdbc.query(SELECT_TELEMETRY, (rs, i) -> new TelemetryPointResponse(
                        toInstant(rs.getTimestamp("bucket")),
                        rs.getObject("cpu", Double.class),
                        rs.getObject("gpu", Double.class),
                        rs.getObject("memory", Double.class),
                        rs.getObject("jetson_temp", Double.class),
                        rs.getObject("battery", Double.class),
                        rs.getObject("temperature", Double.class),
                        rs.getObject("humidity", Double.class),
                        rs.getObject("linear_velocity", Double.class),
                        rs.getObject("angular_velocity", Double.class),
                        rs.getObject("mcu_connected", Boolean.class)),
                bucketSeconds, missionId, fromTs, toTs,
                bucketSeconds, missionId, fromTs, toTs,
                bucketSeconds, missionId, fromTs, toTs);
    }

    /**
     * 임무 궤적 (27.4 {@code trajectory}, S15P11A301-194). 기본은 전 구간이고,
     * maxPoints 를 주면 균등 간격으로 실제 점을 뽑는다 — 구간 평균은 코너를 뭉개서
     * 궤적이 왜곡되므로 쓰지 않는다. 마지막 점(임무 종료 지점)은 항상 포함한다.
     */
    public TrajectoryResponse findTrajectory(UUID missionId, Integer maxPoints) {
        if (maxPoints != null && maxPoints < 2) {
            throw new IllegalArgumentException("maxPoints는 2 이상이어야 합니다.");
        }
        Boolean missionExists = jdbc.queryForObject(
                "SELECT EXISTS(SELECT 1 FROM missions WHERE id = ?)", Boolean.class, missionId);
        if (!Boolean.TRUE.equals(missionExists)) {
            throw new BusinessException(ErrorCode.MISSION_NOT_FOUND);
        }

        List<TrajectoryPointResponse> points = jdbc.query(SELECT_TRAJECTORY,
                (rs, i) -> new TrajectoryPointResponse(
                        toInstant(rs.getTimestamp("time")),
                        rs.getDouble("x"), rs.getDouble("y"), rs.getDouble("yaw")),
                missionId);

        if (maxPoints != null && points.size() > maxPoints) {
            List<TrajectoryPointResponse> sampled = new ArrayList<>();
            int stride = (int) Math.ceil((double) points.size() / maxPoints);
            for (int i = 0; i < points.size(); i += stride) {
                sampled.add(points.get(i));
            }
            if (!sampled.getLast().equals(points.getLast())) {
                sampled.add(points.getLast());
            }
            points = sampled;
        }

        List<UUID> mapIds = jdbc.query(
                "SELECT id FROM maps WHERE mission_id = ? ORDER BY created_at LIMIT 1",
                (rs, i) -> rs.getObject("id", UUID.class), missionId);
        return new TrajectoryResponse(mapIds.isEmpty() ? null : mapIds.getFirst(), points);
    }

    private MissionDetailResponse mapDetail(ResultSet rs) throws SQLException {
        String homePose = rs.getString("home_pose");
        return new MissionDetailResponse(
                rs.getObject("id", UUID.class),
                rs.getString("robot_name"),
                rs.getString("status"),
                toInstant(rs.getTimestamp("started_at")),
                toInstant(rs.getTimestamp("ended_at")),
                rs.getString("end_reason"),
                toInstant(rs.getTimestamp("created_at")),
                homePose == null ? null : (JsonNode) objectMapper.readTree(homePose),
                rs.getObject("duration_sec", Integer.class),
                rs.getObject("distance_m", Double.class),
                rs.getObject("coverage", Double.class),
                rs.getObject("detection_count", Integer.class));
    }

    private Instant toInstant(Timestamp timestamp) {
        return timestamp == null ? null : timestamp.toInstant();
    }
}
