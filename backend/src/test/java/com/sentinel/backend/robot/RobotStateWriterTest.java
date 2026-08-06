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
import org.springframework.messaging.simp.SimpMessagingTemplate;

import com.sentinel.backend.messaging.dto.MessageEnvelope;
import com.sentinel.backend.messaging.dto.RobotStateData;
import com.sentinel.backend.realtime.RealtimeBroadcaster;

/**
 * {@link RobotStateWriter} 시험 (S15P11A301-298).
 *
 * <p>여기서 고정하는 것은 「무엇을 쓰는가」보다 <b>「무엇을 쓰지 않는가」</b>다. 이
 * writer 가 넓어지면 {@code CommandAckWriter} 와 같은 칸을 두고 싸우고, 1Hz heartbeat 가
 * 초당 한 번씩 STOMP 알림을 쏘게 된다.
 *
 * <p>Mockito 대신 손으로 만든 대역을 쓴다. 검사 대상이 "어떤 SQL 을 몇 번 불렀는가"와
 * "broadcast 가 나갔는가" 둘뿐이라 프레임워크가 필요 없고, 그만큼 시험이 무엇을
 * 보장하는지가 읽는 사람에게 그대로 보인다.
 */
class RobotStateWriterTest {

    private static final UUID MISSION = UUID.fromString("4a43f45c-779f-4df5-ac04-1695724829a4");

    /** 호출된 SQL 과 인자를 기록하고, 미리 정한 갱신 행 수를 돌려준다. */
    private static final class RecordingJdbc extends JdbcTemplate {
        private final List<String> sql = new ArrayList<>();
        private final Deque<Integer> updateCounts = new ArrayDeque<>();

        RecordingJdbc(int... counts) {
            for (int count : counts) {
                updateCounts.add(count);
            }
        }

        @Override
        public int update(String statement, Object... args) {
            sql.add(statement);
            return updateCounts.isEmpty() ? 0 : updateCounts.poll();
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
    void manualStateWritesOnceAndBroadcasts() {
        RecordingJdbc jdbc = new RecordingJdbc(1);
        RecordingBroadcaster broadcaster = new RecordingBroadcaster();

        new RobotStateWriter(jdbc, broadcaster).write(envelope(), state("MANUAL", MISSION));

        assertEquals(1, jdbc.sql.size());
        assertTrue(jdbc.sql.getFirst().contains("status = 'MANUAL'"));
        assertEquals(List.of(MISSION + ":MANUAL"), broadcaster.pushed);
    }

    @Test
    void repeatedHeartbeatNeitherWritesNorBroadcasts() {
        // WHERE status <> 'MANUAL' 이 0행을 만든다. 1Hz heartbeat 가 계속 오므로
        // 이 조건이 없으면 초당 한 번씩 의미 없는 STOMP 알림이 나간다.
        RecordingJdbc jdbc = new RecordingJdbc(0);
        RecordingBroadcaster broadcaster = new RecordingBroadcaster();

        new RobotStateWriter(jdbc, broadcaster).write(envelope(), state("MANUAL", MISSION));

        assertTrue(broadcaster.pushed.isEmpty(), "갱신된 행이 없는데 push 가 나갔다");
    }

    @Test
    void leavingManualLandsOnPaused() {
        RecordingJdbc jdbc = new RecordingJdbc(1);
        RecordingBroadcaster broadcaster = new RecordingBroadcaster();

        new RobotStateWriter(jdbc, broadcaster).write(envelope(), state("EXPLORING", MISSION));

        // 젯슨의 EXPLORING 을 그대로 옮기지 않는다. 이 writer 는 수동 여부만 안다.
        assertTrue(jdbc.sql.getFirst().contains("status = 'PAUSED'"));
        assertTrue(jdbc.sql.getFirst().contains("status = 'MANUAL'"),
                "MANUAL 에서 나올 때만 써야 한다 (WHERE 절)");
        assertEquals(List.of(MISSION + ":PAUSED"), broadcaster.pushed);
    }

    @Test
    void endedMissionIsNotResurrected() {
        // ended_at IS NULL 가드가 SQL 에 있어야 한다. 젯슨이 임무 종료 직후에도 잠깐
        // MANUAL 을 보낼 수 있고, 그때 COMPLETED 를 덮으면 임무 이력이 사라진다.
        RecordingJdbc jdbc = new RecordingJdbc(0);
        new RobotStateWriter(jdbc, new RecordingBroadcaster())
                .write(envelope(), state("MANUAL", MISSION));

        assertTrue(jdbc.sql.getFirst().contains("ended_at IS NULL"));
    }

    @Test
    void noActiveMissionDoesNothing() {
        RecordingJdbc jdbc = new RecordingJdbc(1);
        RecordingBroadcaster broadcaster = new RecordingBroadcaster();

        new RobotStateWriter(jdbc, broadcaster).write(envelope(), state("MANUAL", null));

        assertTrue(jdbc.sql.isEmpty(), "진행 중 임무가 없으면 옮겨 적을 곳이 없다");
        assertTrue(broadcaster.pushed.isEmpty());
    }
}
