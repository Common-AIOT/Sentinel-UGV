package com.sentinel.backend.control;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;

import org.junit.jupiter.api.Test;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.messaging.simp.SimpMessagingTemplate;

import com.sentinel.backend.messaging.dto.CommandAckData;
import com.sentinel.backend.messaging.dto.MessageEnvelope;
import com.sentinel.backend.realtime.RealtimeBroadcaster;

/**
 * 모드 전환 ACK 가 {@code missions.status} 에 어떻게 반영되는지 (S15P11A301-298).
 *
 * <p>세 가지를 고정한다.
 *
 * <ol>
 *   <li>MANUAL 은 {@code 'MANUAL'}, AUTO 는 {@code 'PAUSED'} 로 간다 — AUTO 가
 *       EXPLORING 이면 사람이 로봇 옆에 선 채로 탐사가 재개된다(SR-008).</li>
 *   <li>둘 다 {@code started_at}/{@code ended_at}/{@code end_reason}/
 *       {@code mission_results} 를 건드리지 않는다 — 모드 전환은 라이프사이클
 *       이벤트가 아니다.</li>
 *   <li><b>거부된 ACK 는 임무 상태를 건드리지 않는다.</b> 그래야 「자율」이 거부됐을 때
 *       화면이 정직하게 「수동」으로 남는다.</li>
 * </ol>
 */
class CommandAckWriterModeTest {

    private static final UUID MISSION = UUID.fromString("4a43f45c-779f-4df5-ac04-1695724829a4");
    private static final UUID COMMAND = UUID.fromString("7c31a8de-2f64-4b90-9a17-c5e0d8b41f26");

    private static final class RecordingJdbc extends JdbcTemplate {
        private final List<String> sql = new ArrayList<>();
        private final String commandType;

        RecordingJdbc(String commandType) {
            this.commandType = commandType;
        }

        @Override
        public int update(String statement, Object... args) {
            sql.add(statement);
            return 1;
        }

        @Override
        @SuppressWarnings("unchecked")
        public <T> List<T> query(String statement, RowMapper<T> mapper, Object... args) {
            // CommandAckWriter 가 읽는 것은 (mission_id, type) 두 칸뿐이다. RowMapper
            // 를 실제로 부르려면 ResultSet 대역이 필요하므로 행을 직접 만든다.
            return (List<T>) List.of(new CommandAckWriter.CommandRow(MISSION, commandType));
        }

        boolean touched(String fragment) {
            return sql.stream().anyMatch(s -> s.contains(fragment));
        }
    }

    private static final class RecordingBroadcaster extends RealtimeBroadcaster {
        private final List<String> pushed = new ArrayList<>();

        RecordingBroadcaster() {
            super(new SimpMessagingTemplate((message, timeout) -> true));
        }

        @Override
        public void missionStatus(UUID missionId, String status) {
            pushed.add(status);
        }
    }

    private static MessageEnvelope envelope() {
        return new MessageEnvelope("1.0", UUID.randomUUID(), MessageEnvelope.TYPE_COMMAND_ACK,
                "SENTINEL-01", MISSION, 1L, Instant.parse("2026-08-06T01:00:00Z"), null);
    }

    private static CommandAckData ack(String status, String reasonCode) {
        return new CommandAckData(COMMAND, status, reasonCode, null);
    }

    @Test
    void manualAckMovesMissionToManual() {
        RecordingJdbc jdbc = new RecordingJdbc("MANUAL");
        RecordingBroadcaster broadcaster = new RecordingBroadcaster();

        new CommandAckWriter(jdbc, broadcaster).write(envelope(), ack("EXECUTED", null));

        assertTrue(jdbc.touched("status = 'MANUAL'"));
        assertEquals(List.of("MANUAL"), broadcaster.pushed);
        assertLifecycleUntouched(jdbc);
    }

    @Test
    void autoAckLandsOnPausedNotExploring() {
        RecordingJdbc jdbc = new RecordingJdbc("AUTO");
        RecordingBroadcaster broadcaster = new RecordingBroadcaster();

        new CommandAckWriter(jdbc, broadcaster).write(envelope(), ack("EXECUTED", null));

        assertTrue(jdbc.touched("status = 'PAUSED'"));
        assertFalse(jdbc.touched("status = 'EXPLORING'"),
                "AUTO 는 권한 회수일 뿐 주행 재개가 아니다 (SR-008)");
        assertEquals(List.of("PAUSED"), broadcaster.pushed);
        assertLifecycleUntouched(jdbc);
    }

    @Test
    void rejectedAutoAckLeavesMissionStatusAlone() {
        // CommandAckWriter 의 early return 을 고정한다. 이것이 「자율 전환이
        // 거부되었습니다」 뒤에도 화면이 「수동」으로 남는 근거다.
        RecordingJdbc jdbc = new RecordingJdbc("AUTO");
        RecordingBroadcaster broadcaster = new RecordingBroadcaster();

        new CommandAckWriter(jdbc, broadcaster)
                .write(envelope(), ack("REJECTED", "MANUAL_INPUT_ACTIVE"));

        assertEquals(1, jdbc.sql.size(), "control_commands 갱신 하나만 있어야 한다");
        assertTrue(jdbc.sql.getFirst().contains("control_commands"));
        assertTrue(broadcaster.pushed.isEmpty());
    }

    private static void assertLifecycleUntouched(RecordingJdbc jdbc) {
        assertFalse(jdbc.touched("started_at"), "모드 전환은 임무를 시작시키지 않는다");
        assertFalse(jdbc.touched("ended_at"), "모드 전환은 임무를 끝내지 않는다");
        assertFalse(jdbc.touched("end_reason"));
        assertFalse(jdbc.touched("mission_results"));
    }
}
