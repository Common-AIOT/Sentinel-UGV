package com.sentinel.backend.messaging;

import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.Configuration;

/**
 * MQTT 설정 바인딩 활성화. 클라이언트 생명주기는 {@link MqttGateway} 가 관리한다.
 */
@Configuration
@EnableConfigurationProperties(MqttProperties.class)
public class MqttConfig {
}
