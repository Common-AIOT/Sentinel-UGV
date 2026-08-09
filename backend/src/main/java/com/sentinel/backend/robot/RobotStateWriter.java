package com.sentinel.backend.robot;

import java.sql.Timestamp;
import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;

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
 *
 * <h2>예외 하나 — 고아 임무는 이 writer 가 닫는다 (S15P11A301-346)</h2>
 *
 * 위 원칙의 유일한 예외다. 임무 중 스택이 STOP 없이 죽으면 <b>그 임무를 닫는 주체가
 * 아무도 없다</b> — {@code CommandAckWriter} 는 명령 ACK 로만 움직이는데 그 명령이
 * 영영 오지 않기 때문이다. 서버에 {@code ended_at=null} 로 영원히 남고, 관제는 접속할
 * 때마다 그 유령 임무를 이어받아 「탐사중」으로 표시한다 — 로봇은 대기 중인데.
 *
 * <p>그래서 <b>로봇이 살아서 「임무 밖」이라고 말할 때만</b> 닫는다. 「응답 없음」으로는
 * 절대 닫지 않는다 — 무선만 끊긴 경우 로봇은 여전히 주행 중이고 링크가 살아나면 그대로
 * 이어져야 한다. 침묵은 신선도 표시의 몫이지 종료의 근거가 아니다.
 *
 * <p>오판을 막는 조건이 둘이고 <b>둘 다 필수다</b>: 연속 {@link #ORPHAN_CONFIRM}(기동
 * 과도기의 한 프레임짜리 SAFE_IDLE 무시)과 {@link #START_GRACE}(방금 시작한 임무를 즉시
 * 닫지 않기). 종료 사유는 {@code OPERATOR_STOP} 과 구분되는
 * {@link #END_REASON_CONNECTION_LOST} 로 남긴다.
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

    /**
     * 제어 모드(26.2)의 2개. {@code null} 은 「모름」이라 이 집합에 없다 —
     * 저장하지 않고 옛 값을 남긴다.
     */
    private static final Set<String> CONTROL_MODES = Set.of("MANUAL", "AUTO");

    /**
     * 고아 임무를 닫는 사유. {@code OPERATOR_STOP} 과 <b>구분해야 한다</b> —
     * 이력에서 정상 종료와 비정상 종료가 섞이면 「시연 중 몇 번 죽었나」를 셀 수 없다.
     */
    private static final String END_REASON_CONNECTION_LOST = "CONNECTION_LOST";

    /**
     * 이 시간 동안 <b>연속으로</b> 「임무 밖」을 보고해야 임무를 닫는다.
     *
     * <p>기동 과도기의 한 프레임짜리 {@code SAFE_IDLE} 에 반응하지 않기 위해서다.
     * 1Hz heartbeat 이므로 5초는 5프레임이다.
     */
    private static final Duration ORPHAN_CONFIRM = Duration.ofSeconds(5);

    /**
     * 시작 직후 이 시간 안의 임무는 닫지 않는다.
     *
     * <p><b>이 유예가 없으면 방금 시작한 정상 임무를 서버가 즉시 닫는다.</b> START 로
     * 임무 행을 만든 뒤 젯슨이 EXPLORING 으로 전환하기까지 1~2초 동안 로봇 보고는
     * 아직 {@code SAFE_IDLE} 이고, 그것은 위 「임무 밖」 조건과 구별되지 않는다.
     */
    private static final Duration START_GRACE = Duration.ofSeconds(10);

    /**
     * 젯슨이 「임무 밖」이라고 말하는 상태들.
     *
     * <p>젯슨은 이때 {@code activeMissionId} 를 비운다
     * ({@code message_mapper.MISSION_ACTIVE_BY_STATE}). 그런데 DB 에 열린 임무가
     * 남아 있으면 그 임무는 <b>정의상 고아</b>다 — 젯슨이 임무를 복구했다면
     * {@code PAUSED} + missionId 를 보고했을 것이기 때문이다(37-3 복구 규약).
     */
    private static final Set<String> MISSION_ABSENT_STATES = Set.of("SAFE_IDLE", "COMPLETED");

    /**
     * 고아 임무를 닫는다. <b>「응답 없음」으로는 절대 닫지 않는다.</b>
     *
     * <p>닫는 근거는 적극적 증거(로봇이 살아서 「임무 밖」이라고 말한 것)여야 한다.
     * 무선만 끊긴 경우 로봇은 여전히 EXPLORING 을 수행 중이고, 링크가 살아나면 그대로
     * 이어져야 한다 — 침묵은 신선도 표시의 몫이지 임무 종료의 근거가 아니다.
     *
     * <p>{@code started_at} 조건이 시작 유예다. {@code started_at IS NULL} 은 START ACK
     * 이 아직 안 온 임무이며 그것도 닫지 않는다 — 만들어지는 중일 수 있다.
     */
    private static final String CLOSE_ORPHAN = """
            UPDATE missions
               SET status = 'COMPLETED',
                   ended_at = COALESCE(ended_at, ?),
                   end_reason = COALESCE(end_reason, '""" + END_REASON_CONNECTION_LOST + """
            ')
             WHERE id = ? AND ended_at IS NULL
               AND started_at IS NOT NULL AND started_at < ?
            """;

    /**
     * 제어 모드는 <b>임무가 아니라 로봇에</b> 적는다 (S15P11A301-350).
     *
     * <p>2026-08-08 실기동에서 폰이 모터 보드를 수동으로 승격시킨 21:04 부터 14분간 관제
     * 화면이 「자율」을 보여줬다. 원인은 관제가 모드를 아는 유일한 길이
     * {@code missions.status} 였다는 것이다 — 그 경로는 {@code ended_at IS NULL} 가드가
     * 지키는데, <b>임무가 닫힌 뒤에 수동으로 들어가면 옮겨 적을 곳이 없다.</b> 가드는
     * 옳으므로(종료된 임무를 되살리면 안 된다) 값이 갈 자리를 따로 만든다.
     *
     * <p>{@code control_mode <> ?} 대신 {@code IS DISTINCT FROM} 을 쓴다. 초기값이 NULL
     * 이라 {@code <>} 는 NULL 을 돌려주고 {@code DO UPDATE} 가 <b>영원히 실행되지 않는다</b>
     * — 「항상 NULL 인 컬럼」이 되는데, SQL 이 호출되는 것만 보는 시험은 그것을 통과시킨다.
     *
     * <p>{@code status} 는 건드리지 않는다. {@code robots} 행은 이 writer 와
     * {@code RobotPresenceWriter} 가 공유하며 각자 자기 칸만 쓴다. 습관적으로
     * {@code SET status = EXCLUDED.status} 를 넣으면 1Hz 로 오는 이 메시지가 접속 상태를
     * 계속 덮어쓴다.
     */
    private static final String UPSERT_CONTROL_MODE = """
            INSERT INTO robots (name, status, control_mode)
            VALUES (?, 'OFFLINE', ?)
            ON CONFLICT (name) DO UPDATE SET control_mode = EXCLUDED.control_mode
            WHERE robots.control_mode IS DISTINCT FROM EXCLUDED.control_mode
            """;

    private final JdbcTemplate jdbc;
    private final RealtimeBroadcaster broadcaster;
    private final Clock clock;
    /**
     * 로봇별로 「임무 밖」 보고가 시작된 시각 (S15P11A301-346).
     *
     * <p>Paho 콜백은 한 스레드지만 이 맵은 시험에서도 쓰이므로 동시성 자료구조를
     * 쓴다. 로봇 수만큼만 자라고, 임무 안으로 돌아오면 지운다.
     */
    private final Map<String, Instant> orphanSince = new ConcurrentHashMap<>();

    public RobotStateWriter(JdbcTemplate jdbc, RealtimeBroadcaster broadcaster) {
        this(jdbc, broadcaster, Clock.systemUTC());
    }

    /** 시험이 시간을 통제하기 위한 생성자. 5초·10초 조건을 실시간으로 재면 시험이 느려진다. */
    RobotStateWriter(JdbcTemplate jdbc, RealtimeBroadcaster broadcaster, Clock clock) {
        this.jdbc = jdbc;
        this.broadcaster = broadcaster;
        this.clock = clock;
    }

    public void write(MessageEnvelope envelope, RobotStateData data) {
        // 임무 상태보다 **먼저** 쓴다. 아래의 early return 3개는 전부 임무에 관한
        // 것인데(상태 미보고·미지값·열린 임무 없음), 제어 모드는 그 셋 모두에서
        // 유효하다 — 임무 밖에서 사람이 폰을 잡는 것이 정확히 이 값이 필요한
        // 대표 상황이다.
        writeControlMode(envelope.robotId(), data.controlMode());

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
            orphanSince.remove(envelope.robotId());
            return;
        }

        // 고아 임무 종료 (S15P11A301-346). 젯슨이 「임무 밖」인데 DB 에 임무가 열려
        // 있으면, 그 임무를 닫는 주체가 아무도 없어 영원히 남는다 — 관제는 접속할
        // 때마다 그것을 이어받아 「탐사중」으로 표시한다.
        if (data.activeMissionId() == null && MISSION_ABSENT_STATES.contains(reported)) {
            if (closeOrphanIfSustained(envelope.robotId(), missionId, reported)) {
                return;
            }
        } else {
            // 「임무 밖」이 끊기면 처음부터 다시 센다. 한 프레임이라도 임무 안이면
            // 그 임무는 살아 있다.
            orphanSince.remove(envelope.robotId());
        }

        if (jdbc.update(UPDATE_STATUS, reported, missionId, reported) == 0) {
            return;
        }
        log.info("로봇 보고로 임무 상태 반영: missionId={} → {}", missionId, reported);
        // 쓰기가 실제로 일어났을 때만 push 한다. 1Hz heartbeat 가 계속 오므로 조건
        // 없이 push 하면 초당 한 번씩 의미 없는 알림이 나간다.
        broadcaster.missionStatus(missionId, reported);
    }

    /**
     * 제어 모드를 {@code robots} 에 옮긴다. 실패해도 임무 상태 갱신을 막지 않는다.
     *
     * <p><b>예외를 삼키는 것은 배포 순서 때문이다.</b> {@code MqttGateway.messageArrived}
     * 가 이 메서드의 예외를 catch 하면 같은 메시지의 {@code missions.status} 갱신까지
     * 통째로 날아간다 — V10 이 아직 적용되지 않은 환경에서 컬럼 없음(42703)이 나면
     * <b>지금 잘 되는 임무 상태 표시가 회귀한다.</b> 새 기능이 기존 기능을 끌고 내려가지
     * 않도록 여기서 끊는다.
     *
     * <p>{@code null}(모름)은 쓰지 않는다. 젯슨은 {@code mission_manager} 가 없으면
     * null 을 보내는데, 그때 옛 값을 지우면 관제가 「모름」과 「자율」을 구별할 근거를
     * 잃는다 — 값을 지어내지 않는 것과 같은 이유로, 있던 값을 지우지도 않는다.
     */
    private void writeControlMode(String robotName, String controlMode) {
        if (controlMode == null || !CONTROL_MODES.contains(controlMode)) {
            if (controlMode != null) {
                log.warn("모르는 제어 모드를 보고받았다: controlMode={}, robotId={}",
                        controlMode, robotName);
            }
            return;
        }
        try {
            jdbc.update(UPSERT_CONTROL_MODE, robotName, controlMode);
        } catch (RuntimeException e) {
            log.warn("제어 모드를 쓰지 못했다(임무 상태 갱신은 계속한다): robotId={}, controlMode={}",
                    robotName, controlMode, e);
        }
    }

    /**
     * 「임무 밖」이 {@link #ORPHAN_CONFIRM} 이상 지속됐으면 임무를 닫는다.
     *
     * @return 닫았으면 {@code true} — 호출자는 그 임무에 상태를 더 쓰지 않는다.
     */
    private boolean closeOrphanIfSustained(String robotName, UUID missionId, String reported) {
        Instant now = clock.instant();
        Instant since = orphanSince.computeIfAbsent(robotName, key -> now);
        if (Duration.between(since, now).compareTo(ORPHAN_CONFIRM) < 0) {
            return false;
        }

        // 시작 유예는 SQL 이 지킨다 — started_at 이 이 시각보다 오래된 임무만 닫는다.
        Timestamp cutoff = Timestamp.from(now.minus(START_GRACE));
        // 종료 시각은 **지금이 아니라 「임무 밖」이 시작된 시각**이다. 그때까지가
        // 실제 임무 구간이고, 지금으로 적으면 죽어 있던 시간이 임무 시간에 들어간다.
        if (jdbc.update(CLOSE_ORPHAN, Timestamp.from(since), missionId, cutoff) == 0) {
            // 유예 안이거나 이미 닫혔다. 유예 안이면 다음 heartbeat 에 다시 본다.
            return false;
        }

        orphanSince.remove(robotName);
        log.info("고아 임무를 닫았다: missionId={} robotId={} 보고상태={} 종료시각={} 사유={}",
                missionId, robotName, reported, since, END_REASON_CONNECTION_LOST);
        broadcaster.missionStatus(missionId, "COMPLETED");
        return true;
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
