package com.sentinel.backend.robot;

import java.util.UUID;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;

import com.sentinel.backend.messaging.dto.MessageEnvelope;
import com.sentinel.backend.messaging.dto.RobotStateData;
import com.sentinel.backend.realtime.RealtimeBroadcaster;

/**
 * ROBOT_STATE 의 **수동 여부만** {@code missions.status} 에 반영한다 (S15P11A301-298).
 *
 * <h2>왜 필요한가</h2>
 *
 * 수동 전환에는 명령이 없는 경로가 있다. 사람이 폰을 잡으면 모터 보드가 스스로 래치를
 * 걸고 젯슨이 그것을 따라가는데, 그 전이에는 {@code commandId} 가 없어
 * {@code CommandAckWriter} 를 타지 않는다. 그래서 종전에는 로봇이 이미 사람 손에
 * 있는데도 관제 화면은 계속 「탐사 중 / 자율」이었다 — **운영자는 「자동」을 눌러야
 * 한다는 것 자체를 알 수 없었다.**
 *
 * <h2>왜 이렇게 좁은가</h2>
 *
 * 젯슨의 모든 상태를 {@code missions.status} 에 그대로 쓰면 두 가지가 깨진다.
 * {@code CommandAckWriter} 와 같은 칸을 두고 싸워 마지막에 쓴 쪽이 이기고,
 * {@code INTERACTING} 같은 상태가 관제에서 「일시정지」로 보인다(그쪽은 임무 상태가
 * 아니라 대화 중이라는 뜻이다). 이 writer 는 「수동인가 아닌가」 하나만 옮긴다.
 *
 * <p>쓰기가 실제로 일어났을 때만 STOMP 로 push 한다. 1Hz heartbeat 가 계속 오므로
 * 조건 없이 push 하면 초당 한 번씩 의미 없는 알림이 나간다.
 */
@Service
public class RobotStateWriter {

    private static final Logger log = LoggerFactory.getLogger(RobotStateWriter.class);

    /**
     * {@code status <> 'MANUAL'} 조건이 멱등성을 만든다. 1Hz heartbeat 가 같은 값을
     * 계속 실어 와도 두 번째부터는 0행이 갱신되고 broadcast 도 나가지 않는다.
     *
     * <p>{@code ended_at IS NULL} 은 **종료된 임무를 되살리지 않기 위해서**다. 젯슨이
     * 임무 종료 직후에도 잠깐 MANUAL 을 보낼 수 있고, 그때 COMPLETED 를 덮으면
     * 임무 이력이 사라진다.
     */
    private static final String TO_MANUAL = """
            UPDATE missions SET status = 'MANUAL'
            WHERE id = ? AND ended_at IS NULL AND status <> 'MANUAL'
            """;

    /**
     * 반대 방향은 **MANUAL 에서 나올 때만** 쓴다. 젯슨의 EXPLORING·INTERACTING 을
     * 그대로 옮기지 않는 이유는 위 Javadoc 과 같다.
     */
    private static final String FROM_MANUAL = """
            UPDATE missions SET status = 'PAUSED'
            WHERE id = ? AND ended_at IS NULL AND status = 'MANUAL'
            """;

    private final JdbcTemplate jdbc;
    private final RealtimeBroadcaster broadcaster;

    public RobotStateWriter(JdbcTemplate jdbc, RealtimeBroadcaster broadcaster) {
        this.jdbc = jdbc;
        this.broadcaster = broadcaster;
    }

    public void write(MessageEnvelope envelope, RobotStateData data) {
        UUID missionId = data.activeMissionId();
        if (missionId == null) {
            // 진행 중 임무가 없으면 옮겨 적을 곳이 없다. 로봇을 손으로 미는 것은
            // 임무 밖 행위이며 기록 대상이 아니다.
            return;
        }

        boolean manual = RobotStateData.MISSION_STATE_MANUAL.equals(data.missionState());
        int updated = jdbc.update(manual ? TO_MANUAL : FROM_MANUAL, missionId);
        if (updated == 0) {
            return;
        }

        String status = manual ? "MANUAL" : "PAUSED";
        log.info("로봇 상태로 임무 상태 반영: missionId={}, missionState={} → {}",
                missionId, data.missionState(), status);
        broadcaster.missionStatus(missionId, status);
    }
}
