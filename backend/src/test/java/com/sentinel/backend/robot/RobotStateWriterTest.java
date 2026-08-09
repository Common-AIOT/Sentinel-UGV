package com.sentinel.backend.robot;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.sql.Timestamp;
import java.time.Clock;
import java.time.Instant;
import java.time.ZoneId;
import java.time.ZoneOffset;
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
        /** 이 문자열을 담은 SQL 이 오면 던진다. V10 미적용 환경을 흉내낸다. */
        private String failOn;
        /** 고아 종료 UPDATE 가 돌려줄 행 수. 0 이면 「시작 유예에 걸려 안 닫힘」이다. */
        private int orphanCloseRows = 1;

        RecordingJdbc(int... counts) {
            for (int count : counts) {
                updateCounts.add(count);
            }
        }

        RecordingJdbc openMission(UUID missionId) {
            queryResult = List.of(missionId);
            return this;
        }

        RecordingJdbc orphanCloseRows(int rows) {
            orphanCloseRows = rows;
            return this;
        }

        RecordingJdbc failOn(String fragment) {
            failOn = fragment;
            return this;
        }

        @Override
        public int update(String statement, Object... arguments) {
            sql.add(statement);
            args.add(arguments);
            if (failOn != null && statement.contains(failOn)) {
                throw new org.springframework.dao.InvalidDataAccessResourceUsageException(
                        "column \"control_mode\" does not exist");
            }
            // 행 수는 **UPDATE missions 에만** 준다. 큐를 호출 순서대로 소비하면
            // S15P11A301-350 이 더한 robots UPSERT 가 임무 갱신 몫의 1 을 가로채
            // 갱신 0행 → broadcast 없음이 되고, 이 파일의 시험 셋이 조용히 거짓이 된다.
            // 생성자에 숫자를 하나 더 넣어 통과시키면 안 된다 — write() 안의 호출
            // 순서가 바뀔 때마다 시험이 엉뚱한 것을 검사하게 된다.
            if (!statement.contains("UPDATE missions")) {
                return 1;
            }
            // 고아 종료(S15P11A301-346)는 **상태 갱신과 다른 큐를 쓴다.** 둘이 같은
            // 큐를 소비하면 한쪽 호출이 다른 쪽 몫을 가로채, 시험이 검사하려던 것과
            // 다른 것을 검사하게 된다.
            if (statement.contains("CONNECTION_LOST")) {
                return orphanCloseRows;
            }
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

        /** S15P11A301-346 의 고아 임무 종료 UPDATE 가 나갔는지. */
        boolean closedOrphan() {
            return sql.stream().anyMatch(s -> s.contains("CONNECTION_LOST"));
        }

        String orphanSql() {
            return sql.stream().filter(s -> s.contains("CONNECTION_LOST")).findFirst()
                    .orElseThrow(() -> new AssertionError("고아 종료 UPDATE 가 나가지 않았다"));
        }

        Object[] orphanArgs() {
            for (int i = 0; i < sql.size(); i++) {
                if (sql.get(i).contains("CONNECTION_LOST")) {
                    return args.get(i);
                }
            }
            throw new AssertionError("고아 종료 UPDATE 가 나가지 않았다");
        }

        /** S15P11A301-350 의 robots UPSERT 가 나갔는지. */
        boolean upsertedControlMode() {
            return sql.stream().anyMatch(s -> s.contains("INSERT INTO robots"));
        }

        Object[] controlModeArgs() {
            for (int i = 0; i < sql.size(); i++) {
                if (sql.get(i).contains("INSERT INTO robots")) {
                    return args.get(i);
                }
            }
            throw new AssertionError("robots UPSERT 가 나가지 않았다");
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

        // 첫 SQL 은 제어 모드 UPSERT 다(S15P11A301-350). 열린 임무 조회는 그다음이라
        // getFirst() 로 찾지 않는다.
        String lookup = jdbc.sql.stream().filter(s -> s.contains("r.name = ?")).findFirst()
                .orElseThrow(() -> new AssertionError("로봇 이름으로 열린 임무를 찾지 않았다"));
        assertTrue(lookup.contains("ended_at IS NULL"), "열린 임무만 찾는다");
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

        assertTrue(!jdbc.updated(), "모르는 임무 상태는 쓰지 않는다");
        assertTrue(broadcaster.pushed.isEmpty());
    }

    @Test
    void missingStateIsNotWritten() {
        // mission_manager 가 떠 있지 않으면 젯슨도 null 을 보낸다. 값을 지어내지 않는다.
        RecordingJdbc jdbc = new RecordingJdbc(1);
        RecordingBroadcaster broadcaster = new RecordingBroadcaster();

        new RobotStateWriter(jdbc, broadcaster).write(envelope(), state(null, MISSION));

        assertTrue(!jdbc.updated(), "임무 상태를 지어내지 않는다");
        assertTrue(broadcaster.pushed.isEmpty());
    }

    // ── 제어 모드 (S15P11A301-350) ──────────────────────────────────────────

    @Test
    void controlModeIsWrittenWithoutAnyMission() {
        // 이 티켓의 핵심. 2026-08-08 실기동에서 임무가 21:03 에 닫힌 뒤 21:04 에 폰이
        // 보드를 수동으로 승격시켰고, 옮겨 적을 곳이 없어 관제가 14분간 「자율」을
        // 보여줬다. 임무가 없어도 제어 모드는 쓰여야 한다.
        RecordingJdbc jdbc = new RecordingJdbc(1);
        RecordingBroadcaster broadcaster = new RecordingBroadcaster();

        new RobotStateWriter(jdbc, broadcaster).write(envelope(), state("MANUAL", null));

        assertTrue(!jdbc.updated(), "열린 임무가 없으므로 임무 상태는 쓰지 않는다");
        assertTrue(jdbc.upsertedControlMode(), "그래도 제어 모드는 쓴다");
        assertEquals("SENTINEL-01", jdbc.controlModeArgs()[0]);
        assertEquals("MANUAL", jdbc.controlModeArgs()[1]);
    }

    @Test
    void controlModeSurvivesUnknownMissionState() {
        // 임무 상태가 미지값이라 early return 하는 경로에서도 제어 모드는 살아야 한다.
        RecordingJdbc jdbc = new RecordingJdbc(1);

        new RobotStateWriter(jdbc, new RecordingBroadcaster())
                .write(envelope(), state("TELEPORTING", MISSION));

        assertTrue(jdbc.upsertedControlMode());
        assertEquals("AUTO", jdbc.controlModeArgs()[1]);
    }

    @Test
    void nullControlModeDoesNotOverwrite() {
        // mission_manager 가 없으면 젯슨이 null 을 보낸다. 그때 옛 값을 지우면 관제가
        // 「모름」과 「자율」을 구별할 근거를 잃는다 — 값을 지어내지 않는 것과 같은
        // 이유로, 있던 값을 지우지도 않는다.
        RecordingJdbc jdbc = new RecordingJdbc(1);

        new RobotStateWriter(jdbc, new RecordingBroadcaster()).write(envelope(),
                new RobotStateData("SENTINEL-01", "EXPLORING", null, "RUNNING", MISSION, null));

        assertTrue(!jdbc.upsertedControlMode(), "null 은 「모름」이므로 쓰지 않는다");
        assertTrue(jdbc.updated(), "임무 상태는 그대로 쓴다");
    }

    @Test
    void unknownControlModeIsNotWritten() {
        RecordingJdbc jdbc = new RecordingJdbc(1);

        new RobotStateWriter(jdbc, new RecordingBroadcaster()).write(envelope(),
                new RobotStateData("SENTINEL-01", "EXPLORING", "TELEOP", "RUNNING", MISSION, null));

        assertTrue(!jdbc.upsertedControlMode());
    }

    @Test
    void controlModeUpsertDoesNotTouchPresenceStatus() {
        // robots 행은 이 writer 와 RobotPresenceWriter 가 공유한다. 각자 자기 칸만 써야
        // 하는데, SET status = EXCLUDED.status 를 습관적으로 넣으면 1Hz 로 오는 이
        // 메시지가 접속 상태를 계속 덮는다. SQL 두 곳을 나란히 놓고 보지 않으면 안 보인다.
        RecordingJdbc jdbc = new RecordingJdbc(1);

        new RobotStateWriter(jdbc, new RecordingBroadcaster())
                .write(envelope(), state("EXPLORING", MISSION));

        String upsert = jdbc.sql.stream().filter(s -> s.contains("INSERT INTO robots"))
                .findFirst().orElseThrow();
        assertTrue(!upsert.contains("SET status"), "presence 의 status 를 덮지 않는다");
        assertTrue(upsert.contains("IS DISTINCT FROM"),
                "초기값 NULL 에서 <> 는 DO UPDATE 를 영원히 막는다");
    }

    @Test
    void controlModeFailureDoesNotBlockMissionStatus() {
        // V10 이 아직 적용되지 않은 환경에서 컬럼 없음(42703)이 나도 임무 상태 갱신은
        // 계속돼야 한다. MqttGateway 가 예외를 통째로 삼키므로, 여기서 끊지 않으면
        // 지금 잘 되는 임무 상태 표시가 회귀한다.
        RecordingJdbc jdbc = new RecordingJdbc(1).failOn("INSERT INTO robots");
        RecordingBroadcaster broadcaster = new RecordingBroadcaster();

        new RobotStateWriter(jdbc, broadcaster).write(envelope(), state("EXPLORING", MISSION));

        assertEquals(List.of(MISSION + ":EXPLORING"), broadcaster.pushed);
    }

    // ── 고아 임무 종료 (S15P11A301-346) ─────────────────────────────────────
    //
    // 임무 중 스택이 STOP 없이 죽으면 그 임무를 닫는 주체가 아무도 없다. 서버에
    // ended_at=null 로 영원히 남고 관제는 접속할 때마다 그것을 이어받아 「탐사중」을
    // 표시한다. 여기서 고정하는 것은 **닫는 조건이 좁다**는 것이다 — 잘못 닫으면
    // 진행 중인 임무가 사라진다.

    /** 「임무 밖」이 얼마나 지속됐는지를 시험이 통제한다. */
    private static RobotStateWriter writerAt(RecordingJdbc jdbc, RecordingBroadcaster b, Instant now) {
        return new RobotStateWriter(jdbc, b, Clock.fixed(now, ZoneOffset.UTC));
    }

    private static final Instant T0 = Instant.parse("2026-08-09T05:00:00Z");

    @Test
    void orphanIsNotClosedBeforeConfirmWindow() {
        // 기동 과도기의 한 프레임짜리 SAFE_IDLE 로 임무를 닫으면 안 된다.
        RecordingJdbc jdbc = new RecordingJdbc(1).openMission(OPEN_MISSION);
        RecordingBroadcaster broadcaster = new RecordingBroadcaster();
        RobotStateData idle = state("SAFE_IDLE", null);

        writerAt(jdbc, broadcaster, T0).write(envelope(), idle);

        assertTrue(!jdbc.closedOrphan(), "첫 보고만으로 닫으면 안 된다");
    }

    @Test
    void orphanIsClosedAfterSustainedAbsence() {
        RecordingJdbc jdbc = new RecordingJdbc(1).openMission(OPEN_MISSION);
        RecordingBroadcaster broadcaster = new RecordingBroadcaster();
        RobotStateData idle = state("SAFE_IDLE", null);

        // 같은 writer 가 시각만 달리 보도록 clock 을 옮겨 두 번 부른다.
        MutableClock clock = new MutableClock(T0);
        RobotStateWriter writer = new RobotStateWriter(jdbc, broadcaster, clock);
        writer.write(envelope(), idle);          // 관측 시작
        clock.at = T0.plusSeconds(6);            // 5초 초과
        writer.write(envelope(), idle);

        assertTrue(jdbc.closedOrphan(), "지속 확인 뒤에는 닫는다");
        String sql = jdbc.orphanSql();
        assertTrue(sql.contains("CONNECTION_LOST"), "정상 종료와 구분되는 사유여야 한다");
        assertTrue(sql.contains("started_at IS NOT NULL"), "시작 유예가 SQL 에 있어야 한다");
        assertEquals(List.of(OPEN_MISSION + ":COMPLETED"), broadcaster.pushed);
    }

    @Test
    void endedAtIsWhenAbsenceBeganNotNow() {
        // 종료 시각을 「지금」으로 적으면 로봇이 죽어 있던 시간이 임무 시간에 들어간다.
        RecordingJdbc jdbc = new RecordingJdbc(1).openMission(OPEN_MISSION);
        MutableClock clock = new MutableClock(T0);
        RobotStateWriter writer = new RobotStateWriter(jdbc, new RecordingBroadcaster(), clock);
        RobotStateData idle = state("SAFE_IDLE", null);

        writer.write(envelope(), idle);
        clock.at = T0.plusSeconds(30);
        writer.write(envelope(), idle);

        Object[] args = jdbc.orphanArgs();
        assertEquals(Timestamp.from(T0), args[0], "ended_at 은 「임무 밖」이 시작된 시각이다");
    }

    @Test
    void returningToMissionResetsTheOrphanClock() {
        // 한 프레임이라도 임무 안이면 그 임무는 살아 있다 — 처음부터 다시 센다.
        RecordingJdbc jdbc = new RecordingJdbc(1, 1, 1).openMission(OPEN_MISSION);
        MutableClock clock = new MutableClock(T0);
        RobotStateWriter writer = new RobotStateWriter(jdbc, new RecordingBroadcaster(), clock);

        writer.write(envelope(), state("SAFE_IDLE", null));
        clock.at = T0.plusSeconds(3);
        writer.write(envelope(), state("EXPLORING", MISSION));   // 임무 안으로 복귀
        clock.at = T0.plusSeconds(7);
        writer.write(envelope(), state("SAFE_IDLE", null));      // 다시 시작 — 아직 3초

        assertTrue(!jdbc.closedOrphan(), "복귀했으면 누적이 초기화돼야 한다");
    }

    @Test
    void activeMissionIdIsNeverTreatedAsOrphan() {
        // 젯슨이 missionId 를 실어 보내면 임무를 수행 중이라는 뜻이다. SAFE_IDLE 처럼
        // 보여도 닫지 않는다.
        RecordingJdbc jdbc = new RecordingJdbc(1, 1);
        MutableClock clock = new MutableClock(T0);
        RobotStateWriter writer = new RobotStateWriter(jdbc, new RecordingBroadcaster(), clock);

        writer.write(envelope(), state("SAFE_IDLE", MISSION));
        clock.at = T0.plusSeconds(60);
        writer.write(envelope(), state("SAFE_IDLE", MISSION));

        assertTrue(!jdbc.closedOrphan());
    }

    @Test
    void justStartedMissionSurvivesTheStartGrace() {
        // **이 유예가 없으면 방금 시작한 정상 임무를 서버가 즉시 닫는다.** START 로
        // 임무 행을 만든 뒤 젯슨이 EXPLORING 으로 전환하기까지 1~2초 동안 로봇 보고는
        // 아직 SAFE_IDLE 이고, 그것은 「임무 밖」과 구별되지 않는다.
        //
        // 유예는 SQL 의 started_at 조건이 지키므로 0행이 돌아온다. 그때 상태를
        // 계속 관측해야 한다 — 여기서 누적을 지우면 영영 안 닫힌다.
        RecordingJdbc jdbc = new RecordingJdbc(1, 1).openMission(OPEN_MISSION).orphanCloseRows(0);
        RecordingBroadcaster broadcaster = new RecordingBroadcaster();
        MutableClock clock = new MutableClock(T0);
        RobotStateWriter writer = new RobotStateWriter(jdbc, broadcaster, clock);
        RobotStateData idle = state("SAFE_IDLE", null);

        writer.write(envelope(), idle);
        clock.at = T0.plusSeconds(6);
        writer.write(envelope(), idle);

        // SQL 은 나갔지만 0행이다 — 임무는 살아 있고, 상태 갱신은 계속된다.
        assertTrue(jdbc.closedOrphan(), "유예 판정은 SQL 이 한다");
        assertTrue(broadcaster.pushed.stream().noneMatch(p -> p.endsWith(":COMPLETED")),
                "닫히지 않았으면 COMPLETED 를 밀면 안 된다");
    }

    /** 시험에서 시간을 앞으로 돌리기 위한 clock. */
    private static final class MutableClock extends Clock {
        private Instant at;

        MutableClock(Instant at) {
            this.at = at;
        }

        @Override
        public Instant instant() {
            return at;
        }

        @Override
        public ZoneId getZone() {
            return ZoneOffset.UTC;
        }

        @Override
        public Clock withZone(ZoneId zone) {
            return this;
        }
    }
}
