package com.sentinel.backend.realtime;

import org.springframework.context.annotation.Configuration;
import org.springframework.messaging.simp.config.MessageBrokerRegistry;
import org.springframework.web.socket.config.annotation.EnableWebSocketMessageBroker;
import org.springframework.web.socket.config.annotation.StompEndpointRegistry;
import org.springframework.web.socket.config.annotation.WebSocketMessageBrokerConfigurer;

import com.sentinel.backend.common.config.CorsProperties;

/**
 * 관제 웹 실시간 채널 (명세 31-8, S15P11A301-204).
 *
 * <p>연결 엔드포인트 {@code /ws}, 구독 prefix {@code /topic}·{@code /user},
 * 입력 prefix {@code /app}. SockJS 는 쓰지 않는다 — 최신 브라우저의 Native
 * WebSocket 을 사용하고 재연결은 클라이언트가 구현한다(31-8 결정).
 *
 * <p>허용 출처는 REST CORS 와 같은 목록(app.cors.allowed-origins)을 재사용한다 —
 * 도메인이 늘 때 두 곳을 고치게 하면 한쪽을 빠뜨린다.
 */
@Configuration
@EnableWebSocketMessageBroker
public class WebSocketConfig implements WebSocketMessageBrokerConfigurer {

    private final CorsProperties corsProperties;

    public WebSocketConfig(CorsProperties corsProperties) {
        this.corsProperties = corsProperties;
    }

    @Override
    public void registerStompEndpoints(StompEndpointRegistry registry) {
        registry.addEndpoint("/ws")
                .setAllowedOriginPatterns(corsProperties.allowedOrigins().toArray(String[]::new));
    }

    @Override
    public void configureMessageBroker(MessageBrokerRegistry registry) {
        registry.enableSimpleBroker("/topic", "/queue");
        registry.setApplicationDestinationPrefixes("/app");
        registry.setUserDestinationPrefix("/user");
    }
}
