#!/usr/bin/env python3
"""URDF 링크 트리가 끊어지지 않았는지 검사합니다 (S15P11A301-349).

S15P11A301-344 의 마지막 커밋이 주석 블록을 다시 쓰면서 그 아래 있던
`base_link_to_camera_link` 조인트 4줄을 함께 지웠습니다. 새 주석의 닫는 `-->`
가 조인트를 삼켰고, develop 에 그 상태로 병합됐습니다.

**아무 신호가 없었습니다.** URDF 는 XML 로서 여전히 유효하고, 링크
선언(`<link name="camera_link"/>`)도 남아 있어 파일을 훑어보면 정상으로
보입니다. `robot_state_publisher` 는 끊어진 트리를 그대로 받아들여 기동하고,
`base_link -> camera_optical_frame` 조회만 조용히 실패합니다. 그러면 사람
방위각을 지도 좌표로 옮기는 경로(04장 25.3)가 성립하지 않는데, 그 실패는
카메라 화면에도 지도에도 드러나지 않습니다.

`check_urdf` 를 쓰지 않는 이유는 그것이 `liburdfdom-tools` 를 요구해서
rclpy 없는 lint 컨테이너에서 못 돌기 때문입니다. 여기서 잡으려는 결함은
표준 라이브러리 XML 파서로 충분히 잡힙니다.

검사 항목:

1. XML 이 파싱되는지
2. 조인트의 parent/child 가 선언된 링크를 가리키는지
3. **루트(부모 없는 링크)가 정확히 하나인지** — 이번 결함이 걸리는 곳
4. 순환이 없는지
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
URDF_DIR = REPO_ROOT / "jetson/ros2_ws/src/sentinel_description/urdf"


def check(path: Path) -> list[str]:
    """URDF 하나를 검사해 문제 목록을 돌려줍니다. 빈 목록이면 통과입니다."""
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as exc:
        return [f"XML 파싱 실패: {exc}"]

    problems: list[str] = []
    links = {el.get("name") for el in root.findall("link")}

    # child -> (parent, 조인트 이름). child 가 중복되면 트리가 아닙니다.
    parent_of: dict[str, str] = {}
    for joint in root.findall("joint"):
        name = joint.get("name")
        parent_el = joint.find("parent")
        child_el = joint.find("child")
        if parent_el is None or child_el is None:
            problems.append(f"조인트 {name} 에 parent 또는 child 가 없습니다")
            continue
        parent, child = parent_el.get("link"), child_el.get("link")
        for role, link in (("parent", parent), ("child", child)):
            if link not in links:
                problems.append(
                    f"조인트 {name} 의 {role} '{link}' 가 선언되지 않은 링크입니다"
                )
        if child in parent_of:
            problems.append(
                f"링크 {child} 에 부모가 둘입니다 ({parent_of[child]}, {name})"
            )
        else:
            parent_of[child] = parent

    roots = sorted(link for link in links if link not in parent_of)
    if len(roots) != 1:
        # 이번 결함이 걸리는 곳입니다. 링크가 선언만 되고 조인트로 붙지 않으면
        # 여기서 루트가 둘 이상으로 세어집니다.
        problems.append(
            f"루트(부모 없는 링크)가 {len(roots)}개입니다: {', '.join(roots)}. "
            "링크마다 base_link 로 올라가는 조인트가 있어야 합니다"
        )

    # 순환 검사. 위에서 부모 중복을 걸렀으므로 부모를 따라가면 반드시 끝나거나
    # 자기 자신으로 돌아옵니다.
    for link in sorted(links):
        seen = {link}
        cursor = link
        while cursor in parent_of:
            cursor = parent_of[cursor]
            if cursor in seen:
                problems.append(f"링크 {link} 의 부모 사슬에 순환이 있습니다")
                break
            seen.add(cursor)

    return problems


def main() -> int:
    urdfs = sorted(URDF_DIR.glob("*.urdf"))
    if not urdfs:
        print(f"검사할 URDF 가 없습니다: {URDF_DIR}", file=sys.stderr)
        return 1

    failed = False
    for path in urdfs:
        rel = path.relative_to(REPO_ROOT)
        problems = check(path)
        if problems:
            failed = True
            print(f"FAIL {rel}", file=sys.stderr)
            for problem in problems:
                print(f"  - {problem}", file=sys.stderr)
        else:
            print(f"OK   {rel}")

    if failed:
        print(
            "\nURDF 트리가 끊어졌습니다. 조인트가 지워지면 XML 은 유효한 채로 "
            "TF 조회만 조용히 실패합니다 — S15P11A301-349 를 보세요.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
