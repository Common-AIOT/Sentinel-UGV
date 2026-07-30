# sentinel_drive

좌우 모터 구동, 센서 ESP32에서 전달받은 엔코더·차체 IMU 기반 odometry와 최종 `/cmd_vel` 적용을 담당합니다. 센서 bridge는 `/imu/data_raw`를 발행하고 Jetson EKF가 wheel odometry와 융합합니다. 명령 TTL 초과, 종료와 예외 시 모터를 정지합니다.
