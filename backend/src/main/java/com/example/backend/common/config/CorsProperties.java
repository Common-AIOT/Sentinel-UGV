package com.example.backend.common.config;

import java.util.List;

import org.springframework.boot.context.properties.ConfigurationProperties;

/**
 * 브라우저에서 API 를 직접 호출할 수 있는 출처. application.yml 의 {@code app.cors.*} 를 바인딩한다.
 *
 * <p>관제 웹은 Vercel 에, API 는 EC2 에 있어 출처가 다르므로 CORS 없이는 브라우저가 모든 호출을
 * 차단한다.
 */
@ConfigurationProperties(prefix = "app.cors")
public record CorsProperties(
        List<String> allowedOrigins
) {
}
