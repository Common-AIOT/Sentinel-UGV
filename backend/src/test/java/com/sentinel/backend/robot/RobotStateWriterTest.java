package com.sentinel.backend.robot;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.time.Instant;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Deque;
import java.util.List;
import java.util.UUID;

import org.junit.jupiter.api.Test;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.messaging.simp.SimpMessagingTemplate;

import com.sentinel.backend.messaging.dto.MessageEnvelope;
import com.sentinel.backend.messaging.dto.RobotStateData;
import com.sentinel.backend.realtime.RealtimeBroadcaster;

/**
 * {@link RobotStateWriter} 시험 (S15P11A301-316).
 *
 * <p>여기서 고정하는 것은 <b>로봇의 보고가 관제 표시의 근거라는 것</b>과, 그 대가로
 * 깨지기 쉬운 두 가지다 — 1Hz heartbeat 가 초당 한 번 STOMP 알림을 쏘지 않는 것과,
 * 종료된 임무를 되살리지 않는 것.
 *
 * <p>Mockito 대신 손으로 만든 대역을 쓴다. 검사 대상이 "어떤 SQL 을 어떤 인자로 불렀는가"와
 * "broadcast 가 나갔는가" 둘뿐이라 프레임워크가 필요 없고, 그만큼 시험이 무엇을
 * 보장하는지가 읽는 사람에게 그대로 보인다.
 */
class RobotStateWriterTest {

    private static final UUID MISSION = UUID.fromString("4a43f45c-779f-4df5-ac04-1695724829a4");
    private static final UUID OPEN_MISSION = UUID.fromString("9f1c6d0e-2f4b-4a1d-9c3e-5b7a8d2e6f10");

    /** 호출된 SQL·인자를 기록하고, 미리 정한 갱신 행 수와 조회 결과를 돌려준다. */
    private static final class RecordingJdbc extends JdbcTemplate {
        private final List<String> sql = new ArrayList<>();
        private final List<Object[]> args = new ArrayList<>();
        private final Deque<Integer> updateCounts = new ArrayDeque<>();
        private List<?> queryResult = List.of();

        RecordingJdbc(int... counts) {
            for (int count : counts) {
                updateCounts.add(count);
            }
        }

        RecordingJdbc openMission(UUID missionId) {
            queryResult = List.of(missionId);
            return this;
        }

        @Override
        public int update(String statement, Object... arguments) {
            sql.add(statement);
            args.add(arguments);
            return updateCounts.isEmpty() ? 0 : updateCounts.poll();
        }

        @SuppressWarnings("unchecked")
        @Override
        public <T> List<T> query(String statement, RowMapper<T> rowMapper, Object... arguments) {
            sql.add(statement);
            args.add(arguments);
            return (List<T>) queryResult;
        }

        /** 갱신 SQL 은 조회를 거쳤을 수도 있으므로 이름으로 찾는다. */
        String updateSql() {
            return sql.stream().filter(s -> s.contains("UPDATE missions")).findFirst()
                    .orElseThrow(() -> new AssertionError("UPDATE 가 나가지 않았다"));
        }

        Object[] updateArgs() {
            for (int i = 0; i < sql.size(); i++) {
                if (sql.get(i).contains("UPDATE missions")) {
                    return args.get(i);
                }
            }
            throw new AssertionError("UPDATE 가 나가지 않았다");
        }

        boolean updated() {
            return sql.stream().anyMatch(s -> s.contains("UPDATE missions"));
        }
    }

    private static final class RecordingBroadcaster extends RealtimeBroadcaster {
        private final List<String> pushed = new ArrayList<>();

        RecordingBroadcaster() {
            super(new SimpMessagingTemplate((message, timeout) -> true));
        }

        @Override
        public void missionStatus(UUID missionId, String status) {
            pushed.add(missionId + ":" + status);
        }
    }

    private static MessageEnvelope envelope() {
        return new MessageEnvelope("1.0", UUID.randomUUID(), MessageEnvelope.TYPE_STATE,
                "SENTINEL-01", MISSION, 1L, Instant.parse("2026-08-06T01:00:00Z"), null);
    }

    private static RobotStateData state(String missionState, UUID activeMissionId) {
        return new RobotStateData("SENTINEL-01", missionState,
                "MANUAL".equals(missionState) ? "MANUAL" : "AUTO", "RUNNING", activeMissionId, null);
    }

    @Test
    void reportedStateIsWrittenAndBroadcast() {
        RecordingJdbc jdbc = new RecordingJdbc(1);
        RecordingBroadcaster broadcaster = new RecordingBroadcaster();

        new RobotStateWriter(jdbc, broadcaster).write(envelope(), state("EXPLORING", MISSION));

        assertTrue(jdbc.updateSql().contains("SET status = ?"));
        assertEquals("EXPLORING", jdbc.updateArgs()[0]);
        assertEquals(MISSION, jdbc.updateArgs()[1]);
        assertEquals(List.of(MISSION + ":EXPLORING"), broadcaster.pushed);
    }

    @Test
    void manualStillReachesTheConsole() {
        // S15P11A301-298 의 본래 목적. 사람이 폰을 잡으면 명령 없이 수동으로
        // 승격되므로, 이 경로가 유일한 통로다.
        RecordingJdbc jdbc = new RecordingJdbc(1);
        RecordingBroadcaster broadcaster = new RecordingBroadcaster();

        new RobotStateWriter(jdbc, broadcaster).write(envelope(), state("MANUAL", MISSION));

        assertEquals(List.of(MISSION + ":MANUAL"), broadcaster.pushed);
    }

    @Test
    void latchedStateReachesTheConsole() {
        // 종전에는 ESTOP·ERROR 가 어디에도 적히지 않아 화면이 계속 「탐사 중」이었다.
        RecordingJdbc jdbc = new RecordingJdbc(1);
        RecordingBroadcaster broadcaster = new RecordingBroadcaster();

        new RobotStateWriter(jdbc, broadcaster).write(envelope(), state("ESTOP", MISSION));

        assertEquals(List.of(MISSION + ":ESTOP"), broadcaster.pushed);
    }

    @Test
    void repeatedHeartbeatNeitherWritesNorBroadcasts() {
        // WHERE status <> ? 가 0행을 만든다. 1Hz heartbeat 가 계속 오므로 이 조건이
        // 없으면 초당 한 번씩 의미 없는 STOMP 알림이 나간다.
        RecordingJdbc jdbc = new RecordingJdbc(0);
        RecordingBroadcaster broadcaster = new RecordingBroadcaster();

        new RobotStateWriter(jdbc, broadcaster).write(envelope(), state("EXPLORING", MISSION));

        assertEquals("EXPLORING", jdbc.updateArgs()[2], "멱등 조건의 인자가 보고값이어야 한다");
        assertTrue(broadcaster.pushed.isEmpty(), "갱신된 행이 없는데 push 가 나갔다");
    }

    @Test
    void idleWithoutActiveMissionFallsBackToOpenMission() {
        // 젯슨은 임무 밖 상태(SAFE_IDLE·COMPLETED)에서 activeMissionId 를 비운다.
        // 그래도 DB 에 EXPLORING 인 임무가 열려 있으면 그것이 고쳐야 할 대상이다 —
        // 로봇 재시작 후 화면이 계속 「탐사 중」이던 경우다.
        RecordingJdbc jdbc = new RecordingJdbc(1).openMission(OPEN_MISSION);
        RecordingBroadcaster broadcaster = new RecordingBroadcaster();

        new RobotStateWriter(jdbc, broadcaster).write(envelope(), state("SAFE_IDLE", null));

        assertTrue(jdbc.sql.getFirst().contains("r.name = ?"), "로봇 이름으로 열린 임무를 찾는다");
        assertEquals("SENTINEL-01", jdbc.args.getFirst()[0]);
        assertEquals("SAFE_IDLE", jdbc.updateArgs()[0]);
        assertEquals(List.of(OPEN_MISSION + ":SAFE_IDLE"), broadcaster.pushed);
    }

    @Test
    void noOpenMissionDoesNothing() {
        RecordingJdbc jdbc = new RecordingJdbc(1);
        RecordingBroadcaster broadcaster = new RecordingBroadcaster();

        new RobotStateWriter(jdbc, broadcaster).write(envelope(), state("SAFE_IDLE", null));

        assertTrue(!jdbc.updated(), "진행 중 임무가 없으면 옮겨 적을 곳이 없다");
        assertTrue(broadcaster.pushed.isEmpty());
    }

    @Test
    void endedMissionIsNotResurrected() {
        // ended_at IS NULL 가드가 SQL 에 있어야 한다. 젯슨이 임무 종료 직후에도 잠깐
        // 이전 상태를 보낼 수 있고, 그때 COMPLETED 를 덮으면 임무 이력이 사라진다.
        RecordingJdbc jdbc = new RecordingJdbc(0);
        new RobotStateWriter(jdbc, new RecordingBroadcaster())
                .write(envelope(), state("PAUSED", MISSION));

        assertTrue(jdbc.updateSql().contains("ended_at IS NULL"));
    }

    @Test
    void unknownStateIsNotWritten() {
        // 모르는 값을 그대로 쓰면 프런트 라벨 표에서 조용히 빠지고 화면은 옛 상태를
        // 계속 보여준다 — 이 티켓에서 고치는 증상 자체다.
        RecordingJdbc jdbc = new RecordingJdbc(1);
        RecordingBroadcaster broadcaster = new RecordingBroadcaster();

        new RobotStateWriter(jdbc, broadcaster).write(envelope(), state("TELEPORTING", MISSION));

        assertTrue(jdbc.sql.isEmpty());
        assertTrue(broadcaster.pushed.isEmpty());
    }

    @Test
    void missingStateIsNotWritten() {
        // mission_manager 가 떠 있지 않으면 젯슨도 null 을 보낸다. 값을 지어내지 않는다.
        RecordingJdbc jdbc = new RecordingJdbc(1);
        RecordingBroadcaster broadcaster = new RecordingBroadcaster();

        new RobotStateWriter(jdbc, broadcaster).write(envelope(), state(null, MISSION));

        assertTrue(jdbc.sql.isEmpty());
        assertTrue(broadcaster.pushed.isEmpty());
    }
}
