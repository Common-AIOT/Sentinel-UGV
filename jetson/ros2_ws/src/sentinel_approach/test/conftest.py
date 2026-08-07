"""CI(slim 이미지)는 패키지를 설치하지 않고 리포 루트에서 pytest 를 돌린다.

`sentinel_exploration` 과 같은 관행이다 — pytest 가 수집 전에 이 파일을 읽으므로
시험 파일마다 sys.path 를 넣지 않아도 된다.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
