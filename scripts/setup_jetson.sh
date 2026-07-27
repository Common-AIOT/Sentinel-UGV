#!/usr/bin/env bash
set -Eeuo pipefail

mode="apply"

if [[ "${1:-}" == "--help" ]]; then
  echo "Usage: ./scripts/setup_jetson.sh [--check]"
  echo "  (default) Checks the Jetson toolchain and applies required external package patches."
  echo "  --check   Verifies only. Fails if a required patch is missing instead of applying it."
  exit 0
fi

if [[ "${1:-}" == "--check" ]]; then
  mode="check"
fi

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workspace="${repo_root}/jetson/ros2_ws"
patch_directory="${workspace}/patches"

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

# 외부 패키지별 필수 패치 목록. 상세 사유는 jetson/ros2_ws/patches/README.md 참조.
# 형식: "<src 하위 패키지 디렉터리>:<patches 하위 패치 파일명>"
required_patches=(
  "usb_cam:usb_cam-0.8.1-raw-mjpeg-passthrough.patch"
)

patch_failed=0
patch_applied=0

for entry in "${required_patches[@]}"; do
  package_name="${entry%%:*}"
  patch_name="${entry##*:}"
  package_path="${workspace}/src/${package_name}"
  patch_path="${patch_directory}/${patch_name}"

  if [[ ! -f "${patch_path}" ]]; then
    echo "Missing patch file: ${patch_path}" >&2
    patch_failed=1
    continue
  fi

  if [[ ! -d "${package_path}" ]]; then
    echo "Skipping ${package_name}: not imported yet. Run 'vcs import src < sentinel.repos' first." >&2
    patch_failed=1
    continue
  fi

  if git -C "${package_path}" apply --reverse --check "${patch_path}" >/dev/null 2>&1; then
    echo "Patch already applied: ${package_name} <- ${patch_name}"
    continue
  fi

  if ! git -C "${package_path}" apply --check "${patch_path}" >/dev/null 2>&1; then
    echo "Patch does not apply cleanly: ${package_name} <- ${patch_name}" >&2
    echo "  The pinned version in sentinel.repos may have changed." >&2
    echo "  See ${patch_directory}/README.md for the update or removal procedure." >&2
    patch_failed=1
    continue
  fi

  if [[ "${mode}" == "check" ]]; then
    echo "Required patch is NOT applied: ${package_name} <- ${patch_name}" >&2
    echo "  Run ./scripts/setup_jetson.sh to apply it before building." >&2
    patch_failed=1
    continue
  fi

  git -C "${package_path}" apply "${patch_path}"
  echo "Applied patch: ${package_name} <- ${patch_name}"
  patch_applied=1
done

if [[ "${patch_failed}" -ne 0 ]]; then
  echo "External package patch verification failed. Do not build until this is resolved." >&2
  exit 1
fi

if [[ "${patch_applied}" -ne 0 ]]; then
  echo "Patches applied. Rebuild the workspace: colcon build --symlink-install"
fi

echo "External package patch verification passed."
