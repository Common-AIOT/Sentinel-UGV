-- 센서 텔레메트리 확장 (S15P11A301-205).

-- MCU(ESP32) 연결 상태. 젯슨 !133 부터 telemetry health.mcuConnected 에 실값이 온다.
-- 관제가 "온습도가 왜 비었나(센서 문제)"와 "보드가 빠졌나(연결 문제)"를 구분하는
-- 근거라 시계열로 남긴다. true/false/null 3값 — null 은 "확인 수단 없음"이다.
ALTER TABLE robot_metrics ADD COLUMN IF NOT EXISTS mcu_connected BOOLEAN;

-- pose(SLAM)와 motion(엔코더)은 출처가 다른 독립 그룹인데(젯슨 계약: 그룹별 null 은
-- 정상 입력), x/y/yaw NOT NULL 이 "엔코더만 살아 있는" 메시지의 INSERT 를 통째로
-- 실패시켜 같은 메시지의 온습도·MCU 까지 유실됐다. 임무 밖 대기 중(SLAM 꺼짐)에는
-- 이 형태가 기본값이라 허용한다. 좌표 소비자(궤적·이동거리)는 x IS NOT NULL 로 거른다.
ALTER TABLE robot_pose ALTER COLUMN x DROP NOT NULL;
ALTER TABLE robot_pose ALTER COLUMN y DROP NOT NULL;
ALTER TABLE robot_pose ALTER COLUMN yaw DROP NOT NULL;
