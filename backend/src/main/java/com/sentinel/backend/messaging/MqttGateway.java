package com.sentinel.backend.messaging;

import java.nio.charset.StandardCharsets;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;

import org.eclipse.paho.mqttv5.client.IMqttToken;
import org.eclipse.paho.mqttv5.client.MqttActionListener;
import org.eclipse.paho.mqttv5.client.MqttAsyncClient;
import org.eclipse.paho.mqttv5.client.MqttCallback;
import org.eclipse.paho.mqttv5.client.MqttConnectionOptions;
import org.eclipse.paho.mqttv5.client.MqttDisconnectResponse;
import org.eclipse.paho.mqttv5.client.persist.MemoryPersistence;
import org.eclipse.paho.mqttv5.common.MqttException;
import org.eclipse.paho.mqttv5.common.MqttMessage;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

import com.sentinel.backend.encounter.EncounterWriter;
import com.sentinel.backend.messaging.dto.EncounterData;
import com.sentinel.backend.messaging.dto.MessageEnvelope;
import com.sentinel.backend.messaging.dto.PresenceData;
import com.sentinel.backend.messaging.dto.TelemetryData;
import com.sentinel.backend.robot.RobotPresenceWriter;
import com.sentinel.backend.telemetry.TelemetryWriter;

import jakarta.annotation.PostConstruct;
import jakarta.annotation.PreDestroy;
import tools.jackson.databind.ObjectMapper;

/**
 * 젯슨이 발행하는 MQTT 메시지를 구독해 도메인 저장으로 넘긴다 (명세 31-3·31-4).
 *
 * <p>QoS 와 Retain 은 젯슨과 동일한 정책을 쓴다. telemetry 는 QoS 0(유실 허용, 0.5초마다 다음 값),
 * presence 는 QoS 1 + Retain(새 구독자가 붙으면 브로커가 마지막 상태를 즉시 준다).
 *
 * <p>브로커가 아직 떠 있지 않아도 애플리케이션은 기동해야 한다. Paho 의 자동 재연결은 최초 접속이
 * 성공한 뒤에만 동작하므로, 최초 접속 실패는 직접 재시도한다.
 */
@Component
public class MqttGateway implements MqttCallback {

    private static final Logger log = LoggerFactory.getLogger(MqttGateway.class);

    private static final String TOPIC_PREFIX = "sentinel/v1/robots";
    private static final String TELEMETRY_FILTER = TOPIC_PREFIX + "/+/telemetry";
    private static final String PRESENCE_FILTER = TOPIC_PREFIX + "/+/presence";
    // 젯슨은 encounter 를 events 채널로 QoS 1 발행한다 (31-4, sentinel_bridge mqtt_client).
    private static final String EVENTS_FILTER = TOPIC_PREFIX + "/+/events";
    private static final String[] FILTERS = {TELEMETRY_FILTER, PRESENCE_FILTER, EVENTS_FILTER};
    private static final int[] QOS = {0, 1, 1};

    private static final int KEEP_ALIVE_SECONDS = 30;
    private static final long RETRY_DELAY_SECONDS = 10;

    private final MqttProperties props;
    private final ObjectMapper objectMapper;
    private final TelemetryWriter telemetryWriter;
    private final RobotPresenceWriter presenceWriter;
    private final EncounterWriter encounterWriter;

    private final ScheduledExecutorService retryScheduler =
            Executors.newSingleThreadScheduledExecutor(r -> new Thread(r, "mqtt-connect-retry"));

    private MqttAsyncClient client;

    public MqttGateway(MqttProperties props,
                       ObjectMapper objectMapper,
                       TelemetryWriter telemetryWriter,
                       RobotPresenceWriter presenceWriter,
                       EncounterWriter encounterWriter) {
        this.props = props;
        this.objectMapper = objectMapper;
        this.telemetryWriter = telemetryWriter;
        this.presenceWriter = presenceWriter;
        this.encounterWriter = encounterWriter;
    }

    @PostConstruct
    void start() throws MqttException {
        client = new MqttAsyncClient(props.url(), props.clientId(), new MemoryPersistence());
        client.setCallback(this);
        connect();
    }

    @PreDestroy
    void stop() {
        retryScheduler.shutdownNow();
        if (client == null) {
            return;
        }
        try {
            if (client.isConnected()) {
                client.disconnect().waitForCompletion();
            }
            client.close();
        } catch (MqttException e) {
            log.warn("MQTT 종료 중 오류: {}", e.getMessage());
        }
    }

    private MqttConnectionOptions options() {
        MqttConnectionOptions options = new MqttConnectionOptions();
        options.setAutomaticReconnect(true);
        options.setCleanStart(true);
        options.setKeepAliveInterval(KEEP_ALIVE_SECONDS);
        if (props.username() != null && !props.username().isBlank()) {
            options.setUserName(props.username());
            options.setPassword(props.password().getBytes(StandardCharsets.UTF_8));
        }
        return options;
    }

    private void connect() {
        try {
            client.connect(options(), null, new MqttActionListener() {
                @Override
                public void onSuccess(IMqttToken token) {
                    // 구독은 connectComplete 에서 한다. 재연결에도 같은 경로를 타야 한다.
                }

                @Override
                public void onFailure(IMqttToken token, Throwable cause) {
                    log.warn("MQTT 접속 실패({}): {}. {}초 후 재시도",
                            props.url(), cause.getMessage(), RETRY_DELAY_SECONDS);
                    scheduleRetry();
                }
            });
            log.info("MQTT 접속 시도: {}", props.url());
        } catch (MqttException e) {
            log.warn("MQTT 접속 요청 실패: {}. {}초 후 재시도", e.getMessage(), RETRY_DELAY_SECONDS);
            scheduleRetry();
        }
    }

    private void scheduleRetry() {
        if (retryScheduler.isShutdown()) {
            return;
        }
        retryScheduler.schedule(this::connect, RETRY_DELAY_SECONDS, TimeUnit.SECONDS);
    }

    // ── MqttCallback ──────────────────────────────────────────────────────

    @Override
    public void connectComplete(boolean reconnect, String serverURI) {
        try {
            client.subscribe(FILTERS, QOS);
            log.info("MQTT 구독 완료 (reconnect={}): {}", reconnect, String.join(", ", FILTERS));
        } catch (MqttException e) {
            log.error("MQTT 구독 실패: {}", e.getMessage());
        }
    }

    @Override
    public void messageArrived(String topic, MqttMessage message) {
        // 이 메서드에서 예외가 나가면 Paho 가 연결을 끊는다. 한 건의 실패가 수집 전체를
        // 멈추지 않도록 여기서 흡수한다.
        try {
            MessageEnvelope envelope =
                    objectMapper.readValue(message.getPayload(), MessageEnvelope.class);
            switch (envelope.messageType()) {
                case MessageEnvelope.TYPE_TELEMETRY -> telemetryWriter.write(
                        envelope, objectMapper.treeToValue(envelope.data(), TelemetryData.class));
                case MessageEnvelope.TYPE_PRESENCE -> presenceWriter.write(
                        envelope, objectMapper.treeToValue(envelope.data(), PresenceData.class));
                case MessageEnvelope.TYPE_ENCOUNTER -> encounterWriter.write(
                        envelope, objectMapper.treeToValue(envelope.data(), EncounterData.class));
                default -> log.debug("처리 대상이 아닌 messageType: {}", envelope.messageType());
            }
        } catch (Exception e) {
            log.error("MQTT 메시지 처리 실패 (topic={}): {}", topic, e.getMessage());
        }
    }

    @Override
    public void disconnected(MqttDisconnectResponse disconnectResponse) {
        log.warn("MQTT 연결 끊김: {}", disconnectResponse.getReasonString());
    }

    @Override
    public void mqttErrorOccurred(MqttException exception) {
        log.warn("MQTT 오류: {}", exception.getMessage());
    }

    @Override
    public void deliveryComplete(IMqttToken token) {
        // 구독 전용이므로 발행 완료 통지는 쓰지 않는다.
    }

    @Override
    public void authPacketArrived(int reasonCode, org.eclipse.paho.mqttv5.common.packet.MqttProperties properties) {
        // MQTT 5 확장 인증(AUTH)을 쓰지 않는다. 인증은 사용자명·비밀번호로 처리한다.
    }
}
