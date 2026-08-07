// 수동 조종 WiFi 채널 접속 정보 (S15P11A301-298).
//
// **굽기 전에 반드시 자기 폰 핫스팟 값으로 바꾼다.** 플레이스홀더 그대로 구우면
// 접속에 실패하지만 부팅은 정상으로 진행된다(비블로킹) - 즉 조용히 수동 채널만
// 죽어 있고 자율 주행은 멀쩡히 돈다. 그 조합이 가장 찾기 어려우므로
// `/manual/state` 의 `wifi` 필드와 시리얼 없는 진단 경로를 따로 두었다.
//
// 이 값을 리포에 커밋하지 말 것. 폰 핫스팟 비밀번호가 **인증 경계의 전부**다 -
// 로컬 `sid` 는 단일 조종자만 강제하며 신원이 아니다(docs/06 보호 공백 표).
#pragma once

constexpr char MANUAL_WIFI_SSID[] = "***REMOVED-WIFI-SSID***";
constexpr char MANUAL_WIFI_PASSWORD[] = "***REMOVED-WIFI-PASSWORD***";

// mDNS 이름. 폰에서 `http://sentinel-manual.local/` 로 연다. iOS 는 mDNS 를 잘
// 받지만 Android 는 버전에 따라 안 되므로, 안 되면 핫스팟 클라이언트 목록에서
// IP 를 직접 확인해 쓴다.
constexpr char MANUAL_MDNS_HOSTNAME[] = "sentinel-manual";

// HTTP 포트. 80 이 아니면 폰에서 주소에 포트를 붙여야 한다.
constexpr uint16_t MANUAL_WEB_PORT = 80;
