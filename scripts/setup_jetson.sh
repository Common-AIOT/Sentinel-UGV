#!/usr/bin/env bash
set -Eeuo pipefail

if [[ "${1:-}" == "--help" ]]; then
  echo "Usage: ./scripts/setup_jetson.sh"
  echo "Checks the minimum Jetson development toolchain without changing the system."
  exit 0
fi

required_commands=(git python3 colcon rosdep)
missing=0

for command_name in "${required_commands[@]}"; do
  if ! command -v "${command_name}" >/dev/null 2>&1; then
    echo "Missing required command: ${command_name}" >&2
    missing=1
  fi
done

if [[ "${missing}" -ne 0 ]]; then
  echo "Install the missing JetPack/ROS 2 dependencies before continuing." >&2
  exit 1
fi

echo "Jetson development toolchain check passed."
