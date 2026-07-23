<!--
  GENERATED FILE - DO NOT EDIT DIRECTLY.
  Source: docs/specifications/Sentinel_UGV_최종_통합_명세서_v1.0-rc1.md
  Generator: scripts/docs/split-integrated-spec.ps1
-->

> 기준 문서: Sentinel UGV 통합 명세서 v1.0-rc1 (2026-07-22). 변경은 전체본에 반영한 뒤 분할 스크립트를 실행합니다.

# 부록 C. 주요 ROS 토픽·액션
| **이름**            | **형식/방향**                   | **설명**        |
|---------------------|---------------------------------|-----------------|
| /scan               | sensor_msgs/LaserScan           | LiDAR           |
| /camera/image_raw   | sensor_msgs/Image               | 카메라 프레임   |
| /detections         | custom/DetectionArray           | YOLO 결과       |
| /human_observations | custom/HumanObservationArray    | 지도 위치 후보  |
| /imu/data           | sensor_msgs/Imu                 | IMU             |
| /odom               | nav_msgs/Odometry               | 로봇 오도메트리 |
| /map                | nav_msgs/OccupancyGrid          | SLAM 지도       |
| /cmd_vel_nav        | geometry_msgs/Twist             | Nav2 출력       |
| /cmd_vel_manual     | geometry_msgs/Twist             | 수동 입력       |
| /cmd_vel            | geometry_msgs/Twist             | 최종 안전 명령  |
| /stm32/drive_state  | custom/DriveState               | 엔코더·속도·fault |
| /encounters         | custom/Encounter                | 사람 그룹 상태  |
| /interaction/status | custom/InteractionStatus        | 음성 상호작용    |
| /mission/status     | custom/MissionStatus            | 상태 머신       |
| /gimbal/state       | sensor_msgs/JointState          | 짐벌 각도       |
| NavigateToPose      | nav2_msgs/action/NavigateToPose | 목표 주행       |
