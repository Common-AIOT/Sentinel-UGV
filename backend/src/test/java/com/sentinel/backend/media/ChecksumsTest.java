package com.sentinel.backend.media;

import static org.junit.jupiter.api.Assertions.assertEquals;

import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.nio.charset.StandardCharsets;

import org.junit.jupiter.api.Test;

/** 완료 검증(13.6 파일 검증)이 쓰는 스트리밍 해시가 표준 SHA-256 과 일치하는지. */
class ChecksumsTest {

    @Test
    void knownVectorMatches() throws IOException {
        // NIST 표준 벡터: sha256("abc")
        String actual = Checksums.sha256Hex(
                new ByteArrayInputStream("abc".getBytes(StandardCharsets.US_ASCII)));
        assertEquals("ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad", actual);
    }

    @Test
    void emptyStreamMatches() throws IOException {
        String actual = Checksums.sha256Hex(new ByteArrayInputStream(new byte[0]));
        assertEquals("e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855", actual);
    }

    @Test
    void multiBufferInputMatches() throws IOException {
        // 버퍼(8192B)보다 큰 입력이 경계에서 잘못 합쳐지지 않는지 — 'a' 20,000개.
        byte[] input = new byte[20_000];
        java.util.Arrays.fill(input, (byte) 'a');
        String actual = Checksums.sha256Hex(new ByteArrayInputStream(input));
        assertEquals("cc17faaad36649c4603dda4d8ff97cb149722af0bcac0746305a2134ad2d0b97", actual);
    }
}
