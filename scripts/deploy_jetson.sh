#!/usr/bin/env bash
set -Eeuo pipefail

if [[ "${1:-}" == "--help" ]]; then
  echo "Usage: SENTINEL_ESTOP_CONFIRMED=true ./scripts/deploy_jetson.sh"
  echo "Builds and tests the ROS 2 workspace after an explicit physical safety check."
  exit 0
fi

if [[ "${SENTINEL_ESTOP_CONFIRMED:-false}" != "true" ]]; then
  echo "Deployment refused: lift the vehicle, verify the physical E-Stop, then set SENTINEL_ESTOP_CONFIRMED=true." >&2
  exit 1
fi

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
workspace_dir="${SENTINEL_ROS2_WS:-${script_dir}/../jetson/ros2_ws}"

if ! command -v colcon >/dev/null 2>&1; then
  echo "Deployment refused: colcon is not installed." >&2
  exit 1
fi

cd "${workspace_dir}"
colcon build --symlink-install
colcon test
colcon test-result --verbose

echo "Build and tests passed. Service installation remains a hardware-owner operation."
