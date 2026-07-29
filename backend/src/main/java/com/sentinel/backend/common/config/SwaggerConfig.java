package com.sentinel.backend.common.config;

import io.swagger.v3.oas.models.OpenAPI;
import io.swagger.v3.oas.models.info.Contact;
import io.swagger.v3.oas.models.info.Info;
import io.swagger.v3.oas.models.servers.Server;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

import java.util.List;

@Configuration
public class SwaggerConfig {

    @Bean
    public OpenAPI openAPI() {
        return new OpenAPI()
                .info(new Info()
                        .title("Sentinel UGV API")
                        .description("Sentinel UGV 백엔드 REST API 문서")
                        .version("v1.0.0")
                        .contact(new Contact()
                                .name("Team A301")
                                .email("a301@ssafy.com")))
                .servers(List.of(
                        // 운영은 nginx(443) 경유다. 8080 은 보안그룹이 막고 있어 직접 붙을 수 없다.
                        new Server().url("https://api.sentinel-ugv.xyz").description("Production Server"),
                        new Server().url("http://localhost:8080").description("Local Server")));
    }
}
