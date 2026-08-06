# Jetson

Jetson Orin Nano에서 실행되는 로봇 온보드 소프트웨어입니다. 주행·SLAM·탐지·안전 제어는 외부 네트워크와 독립적으로 동작합니다. 음성 인식과 정보 추출은 GPU 서버·LLM을 원격 호출합니다(08장 33).

- `ros2_ws/src/`: ROS 2 패키지 (스트리밍은 `sentinel_streaming`)
- `streaming_poc/`: 카메라·WebRTC PoC 기록 (운영 코드 아님)
- `models/`: 모델 메타데이터, 변환 절차와 체크섬
- `config/`: 장치 및 환경별 설정 템플릿
- `tests/`: 단위, rosbag replay, 하드웨어 벤치 테스트

모터 관련 작업은 차량을 띄운 상태에서 시작하고 E-Stop을 먼저 확인합니다.
