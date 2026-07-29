package com.sentinel.backend.messaging;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.stream.Stream;

import org.junit.jupiter.api.Test;

import com.sentinel.backend.messaging.dto.CommandAckData;
import com.sentinel.backend.messaging.dto.EncounterData;
import com.sentinel.backend.messaging.dto.MessageEnvelope;
import com.sentinel.backend.messaging.dto.MissionCommandData;
import com.sentinel.backend.messaging.dto.PresenceData;
import com.sentinel.backend.messaging.dto.TelemetryData;

import tools.jackson.databind.DeserializationFeature;
import tools.jackson.databind.ObjectMapper;
import tools.jackson.databind.json.JsonMapper;

/**
 * 젯슨과 공유하는 메시지 계약({@code common/schemas})을 백엔드 DTO 가 그대로 파싱하는지 검증한다.
 *
 * <p>CI 의 {@code test:message-contract} job 은 Python 쪽만 검사한다. 젯슨이 필드를 바꾸면
 * Java DTO 는 그대로 통과해 런타임에야 터지므로, 여기서 예제 메시지를 직접 파싱해 막는다.
 *
 * <p>알 수 없는 필드에서 실패하는 기본 설정을 그대로 쓴다. 계약이 {@code additionalProperties:
 * false} 이므로, DTO 가 빠뜨린 필드가 생기면 이 테스트가 깨져야 한다.
 */
class MessageContractTest {

    private static final Path SAMPLES = Path.of("..", "common", "samples");

    // 알 수 없는 필드에서 실패하도록 명시한다. 계약이 additionalProperties: false 이므로
    // DTO 가 빠뜨린 필드가 생기면 이 테스트가 깨져야 한다.
    private final ObjectMapper mapper = JsonMapper.builder()
            .enable(DeserializationFeature.FAIL_ON_UNKNOWN_PROPERTIES)
            .build();

    @Test
    void allSampleMessagesParseIntoCommonEnvelope() throws Exception {
        List<Path> samples = sampleFiles();
        assertFalse(samples.isEmpty(), "no sample messages in common/samples");

        for (Path sample : samples) {
            MessageEnvelope envelope =
                    mapper.readValue(Files.readString(sample), MessageEnvelope.class);
            assertNotNull(envelope.messageId(), sample + ": messageId is empty");
            assertNotNull(envelope.messageType(), sample + ": messageType is empty");
            assertNotNull(envelope.robotId(), sample + ": robotId is empty");
            assertNotNull(envelope.sentAt(), sample + ": failed to parse sentAt");
            assertNotNull(envelope.data(), sample + ": data is empty");
        }
    }

    @Test
    void telemetryWithoutEsp32HasNullBodyGroups() throws Exception {
        TelemetryData data = telemetry("telemetry-esp32-absent.json");

        // 지금 실제로 오는 형태다. null 을 정상 입력으로 다루지 못하면 수집이 전부 실패한다.
        assertNull(data.pose());
        assertNull(data.motion());
        assertNull(data.battery());
        assertNull(data.environment());
        assertNotNull(data.compute(), "compute is always filled by Jetson");
        assertEquals(31.5, data.compute().cpuPercent());

        // false(확인했고 끊김)와 null(확인 수단 없음)은 다르다.
        assertNull(data.health().mcuConnected());
        assertTrue(data.health().lidarOk());
    }

    @Test
    void telemetryWithEsp32FillsAllBodyGroups() throws Exception {
        TelemetryData data = telemetry("telemetry-esp32-connected.json");

        assertEquals(4.31, data.pose().x());
        assertEquals(0.24, data.motion().linearVelocityMps());
        assertEquals(72.0, data.battery().percent());
        assertEquals(28.4, data.environment().temperatureC());
        assertEquals("EXPLORING", data.missionState());
    }

    @Test
    void offMissionTelemetryHasNullMissionId() throws Exception {
        MessageEnvelope envelope = envelope("telemetry-esp32-absent.json");

        // V2 마이그레이션이 hypertable 의 mission_id 를 nullable 로 바꾼 근거다.
        assertNull(envelope.missionId());
    }

    @Test
    void lwtPresenceCarriesOfflineStatusAndReason() throws Exception {
        MessageEnvelope envelope = envelope("presence-offline-lwt.json");
        PresenceData data = mapper.treeToValue(envelope.data(), PresenceData.class);

        assertEquals(PresenceData.STATUS_OFFLINE, data.status());
        assertEquals("MQTT_CONNECTION_LOST", data.reason());
    }

    @Test
    void normalPresenceIsOnline() throws Exception {
        MessageEnvelope envelope = envelope("presence-online.json");
        PresenceData data = mapper.treeToValue(envelope.data(), PresenceData.class);

        assertEquals(PresenceData.STATUS_ONLINE, data.status());
        assertNull(data.reason());
    }

    @Test
    void encounterConfirmedSampleParsesIntoEncounterData() throws Exception {
        MessageEnvelope envelope = envelope("encounter-confirmed.json");
        EncounterData data = mapper.treeToValue(envelope.data(), EncounterData.class);

        assertEquals(EncounterData.PHASE_CONFIRMED, data.phase());
        assertEquals(3, data.personCount());
        assertEquals(List.of(12, 13, 15), data.trackIds());
        assertEquals(4.31, data.pose().x());
        // 신규 적재가 mission_id 를 정하는 경로다. 봉투와 본문 모두에 있어야 한다.
        assertEquals(envelope.missionId(), data.missionId());
        assertNotNull(envelope.missionId());
    }

    @Test
    void encounterEndedSampleHasNullOptionalGroups() throws Exception {
        MessageEnvelope envelope = envelope("encounter-ended.json");
        EncounterData data = mapper.treeToValue(envelope.data(), EncounterData.class);

        // ENDED 는 임무 정보 없이 온다. 갱신 경로가 mission_id 를 요구하면 안 되는 근거다.
        assertEquals(EncounterData.PHASE_ENDED, data.phase());
        assertNull(envelope.missionId());
        assertNull(data.missionId());
        assertNull(data.pose());
        assertNull(data.trackIds());
        assertNull(data.confidence());
        assertEquals(0, data.personCount());
    }

    @Test
    void missionCommandSampleParsesIntoMissionCommandData() throws Exception {
        MessageEnvelope envelope = envelope("mission-command-start.json");
        MissionCommandData data = mapper.treeToValue(envelope.data(), MissionCommandData.class);

        // 서버가 발행하는 봉투다. 젯슨 mission_manager_node 가 이 형태를 받는다.
        assertEquals(MissionCommandData.TYPE_START, data.type());
        assertNotNull(data.commandId());
        assertNotNull(envelope.missionId());
    }

    @Test
    void commandAckSamplesParseIntoCommandAckData() throws Exception {
        CommandAckData accepted = mapper.treeToValue(
                envelope("command-ack-accepted.json").data(), CommandAckData.class);
        assertEquals(CommandAckData.STATUS_ACCEPTED, accepted.status());
        assertNull(accepted.reasonCode());

        CommandAckData rejected = mapper.treeToValue(
                envelope("command-ack-rejected.json").data(), CommandAckData.class);
        assertEquals("REJECTED", rejected.status());
        assertEquals("ESTOP_ACTIVE", rejected.reasonCode());
    }

    private List<Path> sampleFiles() throws Exception {
        try (Stream<Path> files = Files.list(SAMPLES)) {
            return files.filter(p -> p.toString().endsWith(".json")).sorted().toList();
        }
    }

    private MessageEnvelope envelope(String fileName) throws Exception {
        Path sample = SAMPLES.resolve(fileName);
        assertTrue(Files.exists(sample), fileName + " sample is missing");
        return mapper.readValue(Files.readString(sample), MessageEnvelope.class);
    }

    private TelemetryData telemetry(String fileName) throws Exception {
        return mapper.treeToValue(envelope(fileName).data(), TelemetryData.class);
    }
}
