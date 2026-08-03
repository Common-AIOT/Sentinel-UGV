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
}
