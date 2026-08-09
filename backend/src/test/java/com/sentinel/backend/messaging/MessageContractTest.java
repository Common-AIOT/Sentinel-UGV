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
import com.sentinel.backend.messaging.dto.InteractionReportData;
import com.sentinel.backend.messaging.dto.MessageEnvelope;
import com.sentinel.backend.messaging.dto.MissionCommandData;
import com.sentinel.backend.messaging.dto.PresenceData;
import com.sentinel.backend.messaging.dto.RobotStateData;
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
    void legacyTelemetryHasNoRecorderFields() throws Exception {
        // 젯슨 재빌드 전 스택이 보내는 형태다 (S15P11A301-310). 두 필드가 **키 자체로**
        // 없는데, 그것이 파싱을 깨뜨리면 재빌드 순간까지 telemetry 수집이 통째로 멈춘다.
        TelemetryData data = telemetry("telemetry-esp32-connected.json");

        assertNull(data.health().recorderOk());
        assertNull(data.health().recorderLastFailure());
        // 같은 메시지의 나머지가 정상 처리되는지도 함께 본다 — 이 시험의 목적은
        // 「없어도 통과」이지 「두 필드가 null」이 아니다.
        assertTrue(data.health().lidarOk());
    }

    @Test
    void legacyTelemetryHasNoMotorLinkField() throws Exception {
        // recorderOk 와 같은 이유다 (S15P11A301-317). 젯슨 재빌드 전 스택은 이 키를
        // 보내지 않으며, 그것이 파싱을 깨뜨리면 telemetry 수집이 통째로 멈춘다.
        TelemetryData data = telemetry("telemetry-esp32-connected.json");

        assertNull(data.health().motorLinkOk());
        assertTrue(data.health().mcuConnected());
    }

    @Test
    void motorLinkIsSeparateFromSensorBoard() throws Exception {
        // **보드가 둘이다.** mcuConnected 는 엔코더를 내는 센서 보드이고 motorLinkOk 는
        // 바퀴를 돌리는 모터 보드다. 2026-08-06 실기동에서 모터 보드만 죽었는데 화면에
        // 그것을 말하는 값이 없어서, 조작자는 명령을 눌러 거부 알림을 받아야 알았다.
        // 두 값이 한 메시지에서 서로 다를 수 있다는 것이 이 시험의 요점이다.
        TelemetryData data = telemetry("telemetry-motor-link-down.json");

        assertTrue(data.health().mcuConnected());
        assertFalse(data.health().motorLinkOk());
    }

    @Test
    void recorderFailureRidesAlongsideHealthyRecorder() throws Exception {
        // **정상 조합이다.** 젯슨은 성공해도 마지막 실패 사유를 지우지 않는다 — 지우면
        // 간헐 실패가 성공 한 번에 덮여 재발을 못 잡는다(S15P11A301-304 가 19건 쌓이는
        // 동안 드러나지 않은 이유). 「지금은 정상이지만 이번 기동에 실패가 있었다」를
        // 두 필드가 함께 표현하므로 하나로 합치면 안 된다.
        TelemetryData data = telemetry("telemetry-recorder-failed.json");

        assertTrue(data.health().recorderOk());
        assertEquals("RECORDING_FAILED_PTS_REGRESSION", data.health().recorderLastFailure());
    }

    @Test
    void recorderFailureIsPlainStringNotEnum() throws Exception {
        // 젯슨이 RECORDING_FAILED_{사유} 로 만들어 값이 늘어난다. 열거형으로 고정하면
        // 새 사유가 생기는 날 파싱이 깨진다 — 그때는 수집 전체가 멈춘다.
        String json = """
                {"mcuConnected": true, "lidarOk": true, "cameraOk": true,
                 "recorderOk": false, "recorderLastFailure": "RECORDING_FAILED_SOMETHING_NEW"}
                """;
        TelemetryData.Health health = mapper.readValue(json, TelemetryData.Health.class);

        assertEquals("RECORDING_FAILED_SOMETHING_NEW", health.recorderLastFailure());
        // false 는 「실패했다」이고 null 은 「판정 근거 없음」이다. 섞이면 안 된다.
        assertEquals(Boolean.FALSE, health.recorderOk());
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

        // 「자율」 거부의 유일한 정상 사유. 관제가 이 코드로 "조종을 멈추고 다시
        // 시도하세요" 를 띄운다 (S15P11A301-298).
        CommandAckData manualActive = mapper.treeToValue(
                envelope("command-ack-rejected-manual-active.json").data(), CommandAckData.class);
        assertEquals("REJECTED", manualActive.status());
        assertEquals("MANUAL_INPUT_ACTIVE", manualActive.reasonCode());
    }

    @Test
    void modeCommandSampleParsesIntoMissionCommandData() throws Exception {
        MessageEnvelope envelope = envelope("mission-command-auto.json");
        MissionCommandData data = mapper.treeToValue(envelope.data(), MissionCommandData.class);

        assertEquals(MissionCommandData.TYPE_AUTO, data.type());
        assertNotNull(data.commandId());
    }

    @Test
    void robotStateSampleParsesIntoRobotStateData() throws Exception {
        // 종전에는 서버가 이 messageType 을 유일하게 버렸다(MqttGateway default 분기).
        // 이제 RobotStateWriter 가 읽으므로 역직렬화가 실제로 되는지 고정한다
        // (S15P11A301-298).
        MessageEnvelope envelope = envelope("state.json");
        RobotStateData data = mapper.treeToValue(envelope.data(), RobotStateData.class);

        assertEquals(MessageEnvelope.TYPE_STATE, envelope.messageType());
        assertEquals("SAFE_IDLE", data.missionState());
        // 임무 밖이라 둘 다 null 이다. controlMode 가 null 이면 「모름」이므로
        // RobotStateWriter 는 robots.control_mode 를 덮지 않는다 — 있던 값을 지우면
        // 관제가 「모름」과 「자율」을 구별할 근거를 잃는다 (S15P11A301-350).
        assertNull(data.controlMode());
        assertNull(data.activeMissionId());
        assertNotNull(data.components());
    }

    @Test
    void interactionReportSampleParsesIntoInteractionReportData() throws Exception {
        MessageEnvelope envelope = envelope("interaction-report.json");
        InteractionReportData data =
                mapper.treeToValue(envelope.data(), InteractionReportData.class);

        assertEquals(MessageEnvelope.TYPE_INTERACTION_REPORT, envelope.messageType());
        assertEquals(envelope.missionId(), data.missionId());
        assertEquals(3, data.visionPersonCount());
        assertEquals("floor-1", data.encounterPose().mapId());
        assertEquals(1, data.additionalPersonReports().size());
        assertEquals("우리 아기", data.additionalPersonReports().getFirst().subjectText());
        assertEquals("EXACT", data.additionalPersonReports().getFirst().countStatus());
        assertEquals(2, data.additionalPersonReports().getFirst().reportedFloor());
        assertEquals("UNGROUNDED",
                data.additionalPersonReports().getFirst().groundingStatus());
        assertEquals("UNVERIFIED",
                data.additionalPersonReports().getFirst().verificationStatus());
        assertEquals(2, data.sessionReport().reportedResponsiveCount());
        assertEquals("IMMEDIATE", data.riskAssessment().riskLevel());
        assertTrue(data.sessionReport().operatorReviewRequired());
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
