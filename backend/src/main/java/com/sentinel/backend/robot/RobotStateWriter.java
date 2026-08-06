package com.sentinel.backend.robot;

import java.util.Set;
import java.util.UUID;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;

import com.sentinel.backend.messaging.dto.MessageEnvelope;
import com.sentinel.backend.messaging.dto.RobotStateData;
import com.sentinel.backend.realtime.RealtimeBroadcaster;

/**
 * ROBOT_STATE 의 {@code missionState} 를 {@code missions.status} 에 반영한다
 * (S15P11A301-316).
 *
 * <h2>왜 넓혔는가</h2>
 *
 * 종전에는 <b>수동 여부만</b> 옮겼다(S15P11A301-298). 그 결과 {@code missions.status} 는
 * 사실상 <b>「마지막으로 수락된 명령」의 원장</b>이었고, 관제 화면은 그 칸만 읽으므로
 * (프런트 {@code SERVER_MISSION_STATE}) START·RESUME 한 번에 EXPLORING 이 고착됐다.
 * 실제로 이런 일이 났다.
 *
 * <ul>
 *   <li>모드 토글이 {@code MOTOR_BOARD_NO_ACK} 로 거부되면 {@code CommandAckWriter} 는
 *       아무것도 쓰지 않는다 — 화면의 낙관적 「수동 조종」이 백업 폴링에서 「탐사 중」으로
 *       되돌아갔다.</li>
 *   <li>로봇이 스스로 EXPLORING 을 벗어난 것(encounter 진행, 센서 실패 PAUSED,
 *       ESTOP·ERROR, mission_manager 재시작)은 어디에도 적히지 않아 차체가 멈춰 있는데
 *       화면은 계속 「탐사 중」이었다.</li>
 * </ul>
 *
 * 「지금 로봇이 무엇을 하고 있나」의 근거는 명령 이력이 아니라 로봇의 보고여야 한다
 * (27.3). 그 값이 1Hz 로 이미 오고 있었다.
 *
 * <h2>그래도 라이프사이클은 명령 ACK 의 것이다</h2>
 *
 * 이 writer 는 {@code status} 한 칸만 쓴다. {@code started_at}·{@code ended_at}·
 * {@code end_reason}·{@code mission_results} 는 {@code CommandAckWriter} 만 건드린다 —
 * 임무가 시작·종료된 <b>사건</b>은 운영자의 명령이고, 그 사건에 붙는 집계는 한 번만
 * 일어나야 한다. 그래서 두 writer 가 같은 칸을 두고 싸우지 않는다: 명령 ACK 가
 * {@code status} 에 쓰는 값은 로봇이 곧 같은 값을 보고하며 확인해 주고, 어긋나면
 * <b>로봇의 보고가 이긴다</b>.
 *
 * <p>ACK 와 상태 메시지의 순서는 젯슨의 단일 MQTT 연결과 서버의 Paho 콜백 한 스레드가
 * 보존한다({@code MqttGateway.messageArrived}). 즉 START ACK 뒤에 옛 SAFE_IDLE 이
 * 도착해 표시를 되돌리는 경합은 생기지 않는다.
 */
@Service
public class RobotStateWriter {

    private static final Logger log = LoggerFactory.getLogger(RobotStateWriter.class);

    /**
     * 임무 상태 머신(26.2)의 12개. <b>전수를 적고 기본값을 두지 않는다</b> — 모르는
     * 값을 그대로 쓰면 프런트의 라벨 표에서 조용히 빠지고, 화면은 옛 상태를 계속
     * 보여준다. 그것이 이 티켓에서 고치는 증상 자체다. 그래서 모르는 값은 경고로
     * 남기고 쓰지 않는다.
     */
    private static final Set<String> MISSION_STATES = Set.of(
            "SAFE_IDLE", "EXPLORING", "PERSON_APPROACHING", "INTERACTING",
            "POST_RECORDING", "REPORTING", "PAUSED", "MANUAL", "RETURNING",
            "COMPLETED", "ESTOP", "ERROR");

    /**
     * {@code status <> ?} 조건이 멱등성을 만든다. 1Hz heartbeat 가 같은 값을 계속 실어
     * 와도 두 번째부터는 0행이 갱신되고 broadcast 도 나가지 않는다.
     *
     * <p>{@code ended_at IS NULL} 은 <b>종료된 임무를 되살리지 않기 위해서</b>다. 젯슨이
     * 임무 종료 직후에도 잠깐 이전 상태를 보낼 수 있고, 그때 COMPLETED 를 덮으면
     * 임무 이력이 사라진다.
     */
    private static final String UPDATE_STATUS = """
            UPDATE missions SET status = ?
            WHERE id = ? AND ended_at IS NULL AND status <> ?
            """;

    /**
     * {@code activeMissionId} 가 없을 때 쓸 대상을 로봇 이름으로 찾는다.
     *
     * <p>젯슨은 <b>임무 밖 상태(SAFE_IDLE·COMPLETED)에서 {@code activeMissionId} 를 비운다</b>
     * ({@code message_mapper.MISSION_ACTIVE_BY_STATE}). 그것이 없으면 이 경로에서 고칠
     * 수 없는 것이 하나 남는다 — 로봇이 재시작해 SAFE_IDLE 인데 DB 에 EXPLORING 인
     * 임무가 열려 있는 경우다. 로봇은 「나는 임무 밖이다」라고 말하고 있고, 열린 임무는
     * 로봇당 하나뿐이므로(27.3, {@code MissionService.create}) 그 임무가 대상이다.
     */
    private static final String OPEN_MISSION_OF_ROBOT = """
            SELECT m.id FROM missions m
            JOIN robots r ON r.id = m.robot_id
            WHERE r.name = ? AND m.ended_at IS NULL
            """;

    private final JdbcTemplate jdbc;
    private final RealtimeBroadcaster broadcaster;

    public RobotStateWriter(JdbcTemplate jdbc, RealtimeBroadcaster broadcaster) {
        this.jdbc = jdbc;
        this.broadcaster = broadcaster;
    }

    public void write(MessageEnvelope envelope, RobotStateData data) {
        String reported = data.missionState();
        if (reported == null || reported.isBlank()) {
            // mission_manager 가 떠 있지 않으면 젯슨도 null 을 보낸다. 값을 지어내지
            // 않는다 — 관제는 `components.missionManager` 로 이유를 안다.
            return;
        }
        if (!MISSION_STATES.contains(reported)) {
            log.warn("모르는 임무 상태를 보고받았다: missionState={}, robotId={}",
                    reported, envelope.robotId());
            return;
        }

        UUID missionId = data.activeMissionId();
        if (missionId == null) {
            missionId = openMissionOf(envelope.robotId());
        }
        if (missionId == null) {
            // 진행 중 임무가 없으면 옮겨 적을 곳이 없다. 로봇을 손으로 미는 것은
            // 임무 밖 행위이며 기록 대상이 아니다.
            return;
        }

        if (jdbc.update(UPDATE_STATUS, reported, missionId, reported) == 0) {
            return;
        }
        log.info("로봇 보고로 임무 상태 반영: missionId={} → {}", missionId, reported);
        // 쓰기가 실제로 일어났을 때만 push 한다. 1Hz heartbeat 가 계속 오므로 조건
        // 없이 push 하면 초당 한 번씩 의미 없는 알림이 나간다.
        broadcaster.missionStatus(missionId, reported);
    }

    private UUID openMissionOf(String robotName) {
        if (robotName == null) {
            return null;
        }
        return jdbc.query(OPEN_MISSION_OF_ROBOT,
                        (rs, i) -> rs.getObject("id", UUID.class), robotName)
                .stream().findFirst().orElse(null);
    }
}
