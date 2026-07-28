package com.example.backend.messaging;

import org.springframework.boot.context.properties.ConfigurationProperties;

/**
 * MQTT 브로커 연결 설정. application.yml 의 {@code app.mqtt.*} 값을 바인딩한다.
 * 사용자명이 비어 있으면 익명으로 접속한다(로컬 개발용).
 */
@ConfigurationProperties(prefix = "app.mqtt")
public record MqttProperties(
        String url,
        String clientId,
        String username,
        String password
) {
}
