package com.example.backend.common.config;

import java.net.URI;

import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

import software.amazon.awssdk.auth.credentials.AwsBasicCredentials;
import software.amazon.awssdk.auth.credentials.StaticCredentialsProvider;
import software.amazon.awssdk.regions.Region;
import software.amazon.awssdk.services.s3.S3Client;
import software.amazon.awssdk.services.s3.S3Configuration;
import software.amazon.awssdk.services.s3.presigner.S3Presigner;

/**
 * AWS SDK v2 기반 S3 클라이언트/Presigner 설정.
 * 자체 호스팅 MinIO({@code app.s3.endpoint})를 대상으로 하며 path-style 접근을 사용한다.
 */
@Configuration
@EnableConfigurationProperties(S3Properties.class)
public class S3Config {

    private final S3Properties props;

    public S3Config(S3Properties props) {
        this.props = props;
    }

    private StaticCredentialsProvider credentialsProvider() {
        return StaticCredentialsProvider.create(
                AwsBasicCredentials.create(props.accessKey(), props.secretKey()));
    }

    private S3Configuration serviceConfiguration() {
        // MinIO 는 가상 호스트 방식이 아닌 path-style(버킷을 경로로) 접근을 사용한다.
        return S3Configuration.builder()
                .pathStyleAccessEnabled(true)
                .build();
    }

    @Bean
    public S3Client s3Client() {
        return S3Client.builder()
                .endpointOverride(URI.create(props.endpoint()))
                .region(Region.of(props.region()))
                .credentialsProvider(credentialsProvider())
                .serviceConfiguration(serviceConfiguration())
                .build();
    }

    @Bean
    public S3Presigner s3Presigner() {
        return S3Presigner.builder()
                .endpointOverride(URI.create(props.endpoint()))
                .region(Region.of(props.region()))
                .credentialsProvider(credentialsProvider())
                .serviceConfiguration(serviceConfiguration())
                .build();
    }
}
