"""CI(slim 이미지)는 패키지를 설치하지 않고 리포 루트에서 pytest 를 돌린다.

sentinel_bridge·mission 은 시험 파일마다 sys.path 를 넣는 관행인데, 여기는
시험 파일이 4개라 conftest 한 곳에 둔다 — pytest 가 수집 전에 이 파일을
읽으므로 모든 시험에 적용된다.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
