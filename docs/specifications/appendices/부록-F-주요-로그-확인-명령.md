<!--
  GENERATED FILE - DO NOT EDIT DIRECTLY.
  Source: docs/specifications/Sentinel_UGV_최종_통합_명세서_v1.0-rc1.md
  Generator: scripts/docs/split-integrated-spec.ps1
-->

> 기준 문서: Sentinel UGV 통합 명세서 v1.0-rc1 (2026-07-22). 변경은 전체본에 반영한 뒤 분할 스크립트를 실행합니다.

# 부록 F. 주요 로그 확인 명령
```bash
# ROS2
ros2 node list
ros2 topic list
ros2 topic hz /scan
ros2 topic echo /mission/status
ros2 run tf2_tools view_frames
# Jetson
tegrastats
v4l2-ctl --device=/dev/video0 --list-formats-ext
dmesg -w
journalctl -u sentinel-bringup -f
journalctl -u sentinel-stm32-bridge -f
udevadm info /dev/sentinel_mcu
# EC2
sudo docker compose ps
sudo docker compose logs -f backend
sudo docker compose logs -f mediamtx
curl -f https://sentinel.example.com/health
```
