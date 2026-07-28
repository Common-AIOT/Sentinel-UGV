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

# ---------------------------------------------------------------------------
# MediaMTX (S15P11A301-106)
#
# 단일 정적 바이너리이며 저장소에 커밋하지 않는다. 여기서 받아 ~/.local/bin에
# 둔다. 버전을 고정하는 이유는 WHEP 동작과 설정 키가 버전에 따라 바뀌기
# 때문이다.
# ---------------------------------------------------------------------------
MEDIAMTX_VERSION="${MEDIAMTX_VERSION:-1.19.3}"
MEDIAMTX_BIN="${MEDIAMTX_BIN:-${HOME}/.local/bin/mediamtx}"

mediamtx_installed_version() {
  [[ -x "${MEDIAMTX_BIN}" ]] || return 1
  "${MEDIAMTX_BIN}" --version 2>/dev/null | tr -d 'v'
}

installed="$(mediamtx_installed_version || true)"
if [[ "${installed}" == "${MEDIAMTX_VERSION}" ]]; then
  echo "MediaMTX ${MEDIAMTX_VERSION} already installed: ${MEDIAMTX_BIN}"
elif [[ "${mode}" == "check" ]]; then
  echo "MediaMTX ${MEDIAMTX_VERSION}가 설치되지 않았다 (현재: ${installed:-none})." >&2
  echo "  ./scripts/setup_jetson.sh 를 실행해 설치한다." >&2
  exit 1
else
  arch="$(uname -m)"
  case "${arch}" in
    aarch64|arm64) mtx_arch="linux_arm64" ;;
    x86_64)        mtx_arch="linux_amd64" ;;
    *)
      echo "지원하지 않는 아키텍처: ${arch}. MediaMTX를 손으로 설치한다." >&2
      exit 1
      ;;
  esac

  url="https://github.com/bluenviron/mediamtx/releases/download/v${MEDIAMTX_VERSION}/mediamtx_v${MEDIAMTX_VERSION}_${mtx_arch}.tar.gz"
  tmp_dir="$(mktemp -d)"
  trap 'rm -rf "${tmp_dir}"' EXIT

  echo "MediaMTX ${MEDIAMTX_VERSION} 다운로드: ${url}"
  if ! curl -fsSL -o "${tmp_dir}/mediamtx.tar.gz" "${url}"; then
    echo "MediaMTX 다운로드 실패. 망 제약이 있으면 바이너리를 직접 받아" >&2
    echo "  ${MEDIAMTX_BIN} 에 두고 실행 권한을 준다." >&2
    exit 1
  fi

  tar xzf "${tmp_dir}/mediamtx.tar.gz" -C "${tmp_dir}" mediamtx
  mkdir -p "$(dirname "${MEDIAMTX_BIN}")"
  install -m 0755 "${tmp_dir}/mediamtx" "${MEDIAMTX_BIN}"
  echo "MediaMTX 설치 완료: ${MEDIAMTX_BIN} ($("${MEDIAMTX_BIN}" --version))"
fi

# rtspclientsink는 표준 발행 경로다. 없으면 노드가 udp_mpegts로 폴백하지만
# MPEG-TS 먹싱이 한 단계 늘어나므로 설치를 권한다.
if ! gst-inspect-1.0 rtspclientsink >/dev/null 2>&1; then
  echo "경고: rtspclientsink가 없다. sudo apt install -y gstreamer1.0-rtsp 를 권한다." >&2
  echo "      미설치 상태에서는 streaming.launch.py가 udp_mpegts로 폴백한다." >&2
fi

# CI의 lint:shell이 shellcheck로 scripts/*.sh를 검사한다. 로컬에 없으면 CI에서야
# 실패를 알게 되므로 설치를 권한다. sudo 없이 받을 수 있다.
if ! command -v shellcheck >/dev/null 2>&1; then
  sc_version="0.10.0"
  sc_url="https://github.com/koalaman/shellcheck/releases/download/v${sc_version}/shellcheck-v${sc_version}.linux.$(uname -m).tar.xz"
  echo "권장: shellcheck가 없다. CI의 lint:shell을 로컬에서 미리 확인할 수 없다." >&2
  echo "      curl -fsSL -o /tmp/sc.tar.xz '${sc_url}' \\" >&2
  echo "        && tar xf /tmp/sc.tar.xz -C /tmp \\" >&2
  echo "        && install -m755 /tmp/shellcheck-v${sc_version}/shellcheck ~/.local/bin/shellcheck" >&2
fi
