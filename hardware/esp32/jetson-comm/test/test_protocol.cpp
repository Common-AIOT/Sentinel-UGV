// 호스트 컴파일용 프로토콜 유닛테스트. Arduino 의존성이 없으므로 그대로 g++로 빌드된다.
// 빌드: g++ -std=c++17 -I../src test_protocol.cpp ../src/protocol.cpp -o test_protocol && ./test_protocol
//
// 아래 하드코딩된 벡터는 ../test_vectors/crc16_vectors.txt, frame_vectors.txt와 동일한 값이다.
// (두 텍스트 파일은 사람이 읽는 근거 자료로 유지하고, 실행 가능한 테스트는 파일 경로에
//  의존하지 않도록 여기 값을 직접 옮겨 둔다.)

#include <cstdio>
#include <cstring>
#include <string>
#include <vector>

#include "../src/protocol.h"

namespace {

int failureCount = 0;

void expectTrue(bool condition, const char* what) {
  if (!condition) {
    std::printf("FAIL: %s\n", what);
    failureCount++;
  } else {
    std::printf("PASS: %s\n", what);
  }
}

int hexNibble(char c) {
  if (c >= '0' && c <= '9') return c - '0';
  if (c >= 'a' && c <= 'f') return c - 'a' + 10;
  if (c >= 'A' && c <= 'F') return c - 'A' + 10;
  return -1;
}

std::vector<uint8_t> hexToBytes(const char* hex) {
  std::vector<uint8_t> out;
  size_t len = std::strlen(hex);
  for (size_t i = 0; i + 2 <= len; i += 2) {
    int hi = hexNibble(hex[i]);
    int lo = hexNibble(hex[i + 1]);
    out.push_back((uint8_t)((hi << 4) | lo));
  }
  return out;
}

void testCrc16() {
  expectTrue(crc16CcittFalse(nullptr, 0) == 0xFFFF, "crc16 of empty input == 0xFFFF");

  auto check = hexToBytes("313233343536373839");  // ASCII "123456789"
  expectTrue(crc16CcittFalse(check.data(), check.size()) == 0x29B1,
             "crc16 standard check value == 0x29B1");
}

void testCobsRoundTrip(const char* rawHex, const char* encodedHex, const char* label) {
  auto raw = hexToBytes(rawHex);
  auto expectedEncoded = hexToBytes(encodedHex);

  uint8_t encodeBuf[64];
  size_t encodedLen = cobsEncode(raw.data(), raw.size(), encodeBuf, sizeof(encodeBuf));
  bool encodeMatches = encodedLen == expectedEncoded.size() &&
                        std::memcmp(encodeBuf, expectedEncoded.data(), encodedLen) == 0;
  expectTrue(encodeMatches, label);

  uint8_t decodeBuf[64];
  size_t decodedLen = cobsDecode(encodeBuf, encodedLen, decodeBuf, sizeof(decodeBuf));
  bool decodeMatches = decodedLen == raw.size() &&
                        (raw.empty() || std::memcmp(decodeBuf, raw.data(), decodedLen) == 0);
  expectTrue(decodeMatches, (std::string(label) + " round-trip decode").c_str());
}

void testFrameRoundTrip() {
  DriveCommand cmd{};
  cmd.mode = 2;
  cmd.flags = 0x01;
  cmd.targetDriveLeftMmps = -1200;
  cmd.targetDriveRightMmps = 1500;
  cmd.targetSteeringMdeg = 3200;
  cmd.maxAccelMmps2 = 800;
  cmd.maxSteeringRateMdps = 4000;
  cmd.commandTimeoutMs = 300;

  uint8_t payload[DRIVE_COMMAND_BYTES];
  size_t payloadLen = packDriveCommand(cmd, payload);
  expectTrue(payloadLen == DRIVE_COMMAND_BYTES, "packDriveCommand emits expected byte count");

  uint8_t frameBuf[MAX_FRAME_BYTES + 4];
  size_t frameLen = buildFrame(MSG_DRIVE_COMMAND, 42, 123456, payload, (uint16_t)payloadLen,
                                frameBuf, sizeof(frameBuf));
  expectTrue(frameLen > 0 && frameBuf[frameLen - 1] == 0x00,
             "buildFrame produces a 0x00-terminated frame");

  FrameHeader header{};
  uint8_t decodedPayload[MAX_PAYLOAD_BYTES];
  ParseResult result = parseFrame(frameBuf, frameLen - 1, header, decodedPayload, sizeof(decodedPayload));
  expectTrue(result == ParseResult::OK, "parseFrame accepts a valid frame");
  expectTrue(header.messageType == MSG_DRIVE_COMMAND && header.sequence == 42 &&
                 header.senderUptimeMs == 123456 && header.payloadLength == DRIVE_COMMAND_BYTES,
             "parseFrame recovers header fields");

  DriveCommand decoded{};
  bool unpacked = unpackDriveCommand(decodedPayload, header.payloadLength, decoded);
  expectTrue(unpacked && decoded.targetDriveLeftMmps == -1200 && decoded.targetDriveRightMmps == 1500 &&
                 decoded.targetSteeringMdeg == 3200 && decoded.commandTimeoutMs == 300,
             "unpackDriveCommand recovers original values");

  // 손상된 CRC 프레임은 거부되어야 한다.
  frameBuf[frameLen - 3] ^= 0xFF;
  ParseResult corruptedResult = parseFrame(frameBuf, frameLen - 1, header, decodedPayload, sizeof(decodedPayload));
  expectTrue(corruptedResult == ParseResult::BAD_CRC || corruptedResult == ParseResult::COBS_ERROR,
             "parseFrame rejects a corrupted frame (BAD_CRC or COBS_ERROR)");
}

void testSequenceWraparound() {
  expectTrue(isSequenceNewer(5, 4), "sequence 5 is newer than 4");
  expectTrue(!isSequenceNewer(4, 5), "sequence 4 is not newer than 5");
  expectTrue(isSequenceNewer(0, 65535), "sequence 0 is newer than 65535 (wraparound)");
  expectTrue(!isSequenceNewer(5, 5), "identical sequence is not newer");
}

}  // namespace

int main() {
  testCrc16();
  testCobsRoundTrip("", "01", "cobs empty payload");
  testCobsRoundTrip("11", "0211", "cobs single non-zero byte");
  testCobsRoundTrip("1122", "031122", "cobs two non-zero bytes");
  testCobsRoundTrip("110022", "02110222", "cobs embedded zero byte");
  testCobsRoundTrip("0000", "010101", "cobs two consecutive zero bytes");
  testFrameRoundTrip();
  testSequenceWraparound();

  if (failureCount == 0) {
    std::printf("\nAll tests passed.\n");
    return 0;
  }
  std::printf("\n%d test(s) failed.\n", failureCount);
  return 1;
}
