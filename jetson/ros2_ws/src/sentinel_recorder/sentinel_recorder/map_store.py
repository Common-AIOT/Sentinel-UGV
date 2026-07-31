"""SLAM 지도 저장 보관소 (S15P11A301-171, 명세 13.2).

ROS를 모르므로 CI에서 시험할 수 있다. `pending_store`와 같은 원칙이다.

## 왜 로컬에 먼저 쓰는가

31-10이 "Jetson 로컬 파일 — 업로드 대기 영상·지도"를 보존 대상으로 정했다. 재난
현장에서 Wi-Fi가 끊기는 것이 전제이므로, 업로드에 실패해도 지도가 사라지면 안
된다. 이벤트 영상과 같은 취급이다.

## 보고서를 쓰는 이유

업로드는 별 프로세스(또는 나중에 붙는 코드)가 담당한다. 그 경계를 파일로 둔다 —
`report.json`이 있으면 "저장은 끝났고 업로드는 아직"이라는 뜻이고, 업로더는
디렉터리를 훑어 그것만 보면 된다. `sentinel_recorder`가 이벤트에 쓰는 방식과
같아서 나중에 업로더를 합칠 수 있다.

지도 업로드 API는 아직 백엔드에 없다(2026-07-30 확인, maps 엔드포인트 0건).
그래서 이 모듈은 저장·보존까지만 하고 업로드는 API가 생기면 붙인다.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PGM_NAME = 'map.pgm'
YAML_NAME = 'map.yaml'
REPORT_NAME = 'report.json'

UPLOAD_STATE_PENDING = 'UPLOAD_PENDING'
UPLOAD_STATE_AVAILABLE = 'AVAILABLE'

# 임무 밖에서 지도를 저장할 때 쓰는 디렉터리 이름. missionId가 없으면
# 백엔드 maps 행을 만들 수 없지만(mission_id가 NOT NULL FK), 파일은 남겨야
# 한다 — 개발 중에는 관제 없이 젯슨만 띄우는 일이 잦고 그때 만든 지도도
# 사람이 열어볼 값이 있다.
NO_MISSION_DIRNAME = 'no-mission'


def write_report(path: Path, report: dict[str, Any]) -> None:
    """보고서를 원자적으로 쓴다.

    이벤트 보고서에서 겪은 것과 같은 이유다(S15P11A301-124). 업로더가 읽는 중에
    덮어쓰면 잘린 JSON을 보고, 그것을 영구 실패로 굳혀 다시는 올리지 않았다.
    `fsync`까지 하는 것은 microSD에서 전원이 끊기면 이름만 바뀌고 내용이
    0바이트일 수 있기 때문이다.
    """
    temporary = path.with_suffix(path.suffix + '.tmp')
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    with temporary.open('w', encoding='utf-8') as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


@dataclass
class SavedMap:
    directory: Path
    pgm_bytes: int
    yaml_bytes: int
    upload_state: str

    @property
    def complete(self) -> bool:
        """두 파일이 다 있고 비어 있지 않은가.

        pgm만 있고 yaml이 없으면 못 쓴다 — yaml에 해상도와 원점이 있어야
        관제가 좌표를 지도 위에 얹을 수 있다(S15P11A301-170의 pose가 그
        좌표계다).
        """
        return self.pgm_bytes > 0 and self.yaml_bytes > 0


class MapStore:
    """임무별 지도 디렉터리를 관리한다."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def prepare(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    def directory_for(self, mission_id: str | None) -> Path:
        return self.root / (mission_id or NO_MISSION_DIRNAME)

    def basename_for(self, mission_id: str | None) -> Path:
        """`save_map` 서비스에 넘길 확장자 없는 경로.

        slam_toolbox가 여기에 `.pgm`과 `.yaml`을 붙여 쓴다. 절대경로를 줘야
        한다 — 상대경로면 노드의 작업 디렉터리에 쓰이고, launch로 띄운 노드의
        작업 디렉터리는 예측할 수 없다.
        """
        return self.directory_for(mission_id) / 'map'

    def scan(self, mission_id: str | None) -> SavedMap | None:
        """저장 결과를 읽는다. 파일이 없으면 None."""
        directory = self.directory_for(mission_id)
        pgm = directory / PGM_NAME
        yaml_path = directory / YAML_NAME
        if not pgm.exists() and not yaml_path.exists():
            return None

        upload_state = UPLOAD_STATE_PENDING
        try:
            report = json.loads(
                (directory / REPORT_NAME).read_text(encoding='utf-8')
            )
            upload_state = str(report.get('uploadState', UPLOAD_STATE_PENDING))
        except (OSError, json.JSONDecodeError):
            pass

        return SavedMap(
            directory=directory,
            pgm_bytes=pgm.stat().st_size if pgm.exists() else 0,
            yaml_bytes=yaml_path.stat().st_size if yaml_path.exists() else 0,
            upload_state=upload_state,
        )

    def read_report(self, mission_id: str | None) -> dict[str, Any] | None:
        """보고서를 읽는다. 없거나 깨졌으면 None.

        깨진 보고서를 None으로 돌리는 것은 업로더가 "보고서 없음"과 같게
        취급하게 하려는 것이다 — 그쪽이 다시 올리는 쪽으로 기운다. 이미 올라간
        지도를 한 번 더 올리는 것보다 못 올리는 것이 나쁘다.
        """
        path = self.directory_for(mission_id) / REPORT_NAME
        try:
            payload = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

    def iter_missions(self) -> list[str]:
        """지도가 저장된 임무 ID들. 업로드 대상 후보다.

        `no-mission`은 제외한다. 백엔드 maps.mission_id가 NOT NULL FK이므로
        임무 없이 만든 지도는 등록할 수 없다. 파일은 남겨 두고 사람이 열어볼 수
        있게만 한다.

        디렉터리 이름이 곧 missionId다. UUID 형식을 검사하지는 않는다 — 검사해
        걸러내면 형식이 바뀌었을 때 조용히 아무것도 안 올린다. 잘못된 값은
        발급 요청이 4xx로 되돌려주고, 그것은 재시도하지 않는 실패로 기록된다.
        """
        if not self.root.exists():
            return []
        names = []
        for entry in sorted(self.root.iterdir()):
            if not entry.is_dir() or entry.name == NO_MISSION_DIRNAME:
                continue
            names.append(entry.name)
        return names

    def build_report(
        self,
        *,
        mission_id: str | None,
        saved: SavedMap,
        saved_at: str,
        resolution: float | None,
        origin: list[float] | None,
    ) -> dict[str, Any]:
        """업로더가 읽을 보고서. 13.2의 maps 행에 필요한 것을 담는다."""
        return {
            'schemaVersion': '1.0',
            'missionId': mission_id,
            'savedAt': saved_at,
            'uploadState': UPLOAD_STATE_PENDING,
            'files': {
                'pgm': PGM_NAME,
                'yaml': YAML_NAME,
                'pgmSizeBytes': saved.pgm_bytes,
                'yamlSizeBytes': saved.yaml_bytes,
            },
            # 관제가 좌표를 얹는 데 필요한 값이다. yaml 안에도 있지만 여기에
            # 두면 업로더와 관제가 yaml을 파싱하지 않아도 된다.
            'resolution': resolution,
            'origin': origin,
        }


def read_map_yaml(path: Path) -> tuple[float | None, list[float] | None]:
    """지도 yaml에서 해상도와 원점만 꺼낸다.

    `yaml` 모듈을 쓰지 않는다. 이 파일은 slam_toolbox가 만드는 고정 형식이고
    (`resolution: 0.05`, `origin: [-7.36, -7.94, 0]`), 값 두 개를 위해 의존성을
    더할 이유가 없다. 형식이 바뀌면 None을 돌려주고 호출자가 로그를 남긴다.
    """
    resolution: float | None = None
    origin: list[float] | None = None
    try:
        for line in path.read_text(encoding='utf-8').splitlines():
            stripped = line.strip()
            if stripped.startswith('resolution:'):
                resolution = float(stripped.split(':', 1)[1].strip())
            elif stripped.startswith('origin:'):
                raw = stripped.split(':', 1)[1].strip().strip('[]')
                origin = [float(part) for part in raw.split(',')]
    except (OSError, ValueError, IndexError):
        return None, None
    return resolution, origin
