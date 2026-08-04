package com.sentinel.backend.realtime;

import java.time.Instant;
import java.util.UUID;

import org.springframework.messaging.simp.SimpMessagingTemplate;
import org.springframework.stereotype.Service;

/**
 * 관제 웹으로의 STOMP 브로드캐스트 창구 (31-8, S15P11A301-204).
 *
 * <p>MQTT 수신부(ACK·encounter 적재)가 DB 에 쓴 "직후" 호출한다 — DB 가 진실이고
 * 이 채널은 알림이다. 브라우저가 하나도 안 붙어 있어도 발행은 무해하다(SimpleBroker).
 */
@Service
public class RealtimeBroadcaster {

    private final SimpMessagingTemplate messaging;

    public RealtimeBroadcaster(SimpMessagingTemplate messaging) {
        this.messaging = messaging;
    }

    /** 임무 상태 전이 알림 → {@code /topic/missions/{id}/events} */
    public void missionStatus(UUID missionId, String status) {
        messaging.convertAndSend("/topic/missions/" + missionId + "/events",
                new MissionEventMessage("MISSION_STATUS", missionId, status, Instant.now()));
    }

    /** 발견 생성·phase 변화 알림 → {@code /topic/missions/{id}/encounters} */
    public void encounterChanged(UUID missionId, EncounterChangedMessage message) {
        messaging.convertAndSend("/topic/missions/" + missionId + "/encounters", message);
    }

    /**
     * 음성 보고 갱신 신호 (S15P11A301-243) — 같은 encounters 토픽에 phase 만 다르게 싣는다.
     *
     * <p>내용은 싣지 않는다. 보고 스키마를 REST 와 WS 두 곳에서 관리하지 않기 위한
     * 합의로, 관제는 이 신호를 받으면 발견 상세를 다시 조회한다. 음성 세션은 발견
     * 확정 뒤에 끝나므로 CONFIRMED 알림만으로는 갱신 시점을 알 수 없다.
     */
    public void interactionReported(UUID missionId, UUID encounterId, Instant at) {
        messaging.convertAndSend("/topic/missions/" + missionId + "/encounters",
                new EncounterChangedMessage(encounterId, PHASE_INTERACTION_REPORTED,
                        null, null, null, at));
    }

    /** 젯슨 phase 5종과 겹치지 않는 서버 발신 전용 값. 관제는 이 값이면 상세를 재조회한다. */
    public static final String PHASE_INTERACTION_REPORTED = "INTERACTION_REPORTED";
}
