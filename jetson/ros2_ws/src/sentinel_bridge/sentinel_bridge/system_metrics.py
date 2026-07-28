"""젯슨 자체 지표 수집 (S15P11A301-128).

`telemetry.compute`를 채운다. 이 값들은 ESP32와 무관하게 젯슨이 스스로 알기
때문에 지금부터 실제 값을 보낼 수 있다.

psutil 같은 의존성을 쓰지 않고 sysfs와 procfs를 직접 읽는다. 추가 패키지 없이
동작해야 배포가 단순하고, Tegra는 NVML이 없어 표준 GPU 라이브러리가 어차피
동작하지 않는다(S15P11A301-62에서 확인).

2026-07-28 이 젯슨에서 실제로 읽히는 경로만 사용한다.

    /proc/stat                          CPU 누적 시간
    /proc/meminfo                       MemTotal, MemAvailable
    /sys/devices/platform/gpu.0/load    GPU 부하 (천분율)
    thermal_zone0 (cpu-thermal)         온도 (밀리도)

경로가 없으면 예외를 던지지 않고 None을 돌려준다. 지표 수집 실패가 관제 링크를
끊을 이유는 없다.
"""

from __future__ import annotations

from pathlib import Path

PROC_STAT = Path("/proc/stat")
PROC_MEMINFO = Path("/proc/meminfo")

# Tegra GPU 부하. 0~1000 천분율이므로 10으로 나눈다.
GPU_LOAD_CANDIDATES = (
    Path("/sys/devices/platform/gpu.0/load"),
    Path("/sys/devices/gpu.0/load"),
    Path("/sys/class/devfreq/17000000.gpu/device/load"),
)

THERMAL_ROOT = Path("/sys/devices/virtual/thermal")
# cpu-thermal을 대표 온도로 쓴다. cv0~cv2 존은 값이 비어 있는 경우가 있다.
PREFERRED_THERMAL_TYPES = ("cpu-thermal", "soc0-thermal", "gpu-thermal")


def _read_text(path: Path) -> str | None:
    """sysfs를 안전하게 읽는다.

    `OSError`만 잡으면 부족하다. 이 젯슨의 `cv0-thermal`~`cv2-thermal` 존은
    파일이 존재하고 읽기 권한도 있는데 커널이 내용을 주지 않아, 디코딩 단계에서
    `TypeError: can't concat NoneType to bytes`가 난다. 지표 수집 실패가 관제
    링크를 끊을 이유는 없으므로 조용히 None을 돌려준다.
    """
    try:
        return path.read_text(encoding="utf-8").strip()
    except (OSError, TypeError, UnicodeDecodeError):
        return None


class CpuSampler:
    """/proc/stat 차분으로 CPU 사용률을 계산한다.

    누적값이므로 두 시점의 차이가 필요하다. 첫 호출은 기준점만 잡고 None을
    돌려준다. 순간값을 억지로 만들어내면 첫 telemetry가 거짓말을 한다.
    """

    def __init__(self) -> None:
        self._previous: tuple[int, int] | None = None

    def sample(self) -> float | None:
        line = _read_text(PROC_STAT)
        if not line:
            return None
        first = line.splitlines()[0].split()
        if len(first) < 5 or first[0] != "cpu":
            return None

        values = [int(v) for v in first[1:]]
        total = sum(values)
        # user, nice, system 다음이 idle이고 그다음이 iowait이다. idle과 iowait을
        # 합쳐 유휴로 본다.
        idle = values[3] + (values[4] if len(values) > 4 else 0)

        if self._previous is None:
            self._previous = (total, idle)
            return None

        previous_total, previous_idle = self._previous
        total_delta = total - previous_total
        idle_delta = idle - previous_idle
        self._previous = (total, idle)

        if total_delta <= 0:
            return None
        usage = (1.0 - idle_delta / total_delta) * 100.0
        return round(max(0.0, min(100.0, usage)), 1)


def memory_percent() -> float | None:
    text = _read_text(PROC_MEMINFO)
    if not text:
        return None
    total = available = None
    for line in text.splitlines():
        if line.startswith("MemTotal:"):
            total = int(line.split()[1])
        elif line.startswith("MemAvailable:"):
            available = int(line.split()[1])
        if total is not None and available is not None:
            break
    if not total or available is None:
        return None
    return round((1.0 - available / total) * 100.0, 1)


def gpu_percent() -> float | None:
    for path in GPU_LOAD_CANDIDATES:
        raw = _read_text(path)
        if raw is None:
            continue
        try:
            permille = int(raw)
        except ValueError:
            continue
        return round(max(0.0, min(100.0, permille / 10.0)), 1)
    return None


def jetson_temperature_c() -> float | None:
    if not THERMAL_ROOT.is_dir():
        return None
    zones: dict[str, float] = {}
    for zone in sorted(THERMAL_ROOT.glob("thermal_zone*")):
        zone_type = _read_text(zone / "type")
        raw = _read_text(zone / "temp")
        if not zone_type or not raw:
            continue
        try:
            zones[zone_type] = int(raw) / 1000.0
        except ValueError:
            continue
    if not zones:
        return None
    for preferred in PREFERRED_THERMAL_TYPES:
        if preferred in zones:
            return round(zones[preferred], 1)
    # 선호 존이 없으면 가장 뜨거운 값을 쓴다. 과열 감지가 목적이므로 최댓값이 맞다.
    return round(max(zones.values()), 1)


class ComputeMetrics:
    """`telemetry.compute` 본문을 만든다."""

    def __init__(self) -> None:
        self._cpu = CpuSampler()

    def sample(self) -> dict[str, float | None] | None:
        cpu = self._cpu.sample()
        memory = memory_percent()
        # 스키마가 cpuPercent와 memoryPercent를 필수로 요구한다. 둘 중 하나라도
        # 없으면 compute 전체를 null로 보낸다. 필수 필드를 null로 채워 스키마를
        # 위반하는 것보다 낫다.
        if cpu is None or memory is None:
            return None
        return {
            "cpuPercent": cpu,
            "gpuPercent": gpu_percent(),
            "memoryPercent": memory,
            "jetsonTempC": jetson_temperature_c(),
        }
