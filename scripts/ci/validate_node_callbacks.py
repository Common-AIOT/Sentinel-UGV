#!/usr/bin/env python3
"""ROS 노드가 넘긴 콜백이 실제로 정의돼 있는지 검사한다 (S15P11A301-226).

`rclpy`를 import하지 않는다. 그래서 CI 컨테이너(`python:3.10-alpine`)에서 돈다 —
이 저장소의 파이썬 시험이 `message_mapper`·`mqtt_client`처럼 ROS를 모르는 모듈만
다루는 것과 같은 이유다(S15P11A301-128·135).

## 왜 필요한가

`cloud_bridge`가 `develop`에서 기동 즉시 죽고 있었다.

    AttributeError: 'CloudBridgeNode' object has no attribute '_on_candidates'

S15P11A301-193이 다른 메서드를 추가하면서 `_on_candidates`를 덮어써 지웠는데
구독 등록은 그대로 남았다. `create_subscription(..., self._on_candidates, ...)`을
평가하는 순간 죽는다.

**아무 신호가 없었다.** CI는 노드 클래스를 시험하지 않고, `demo.launch.py`는 노드
하나가 죽어도 나머지를 계속 돌리며(32장 장애 격리, 의도된 동작), 관제 화면은 값이
멈춘 것을 목업과 구분해 보여주지 않았다. 관제로 가는 telemetry·state·presence·
encounter가 전부 끊긴 채로 스택이 "정상"으로 보였다.

타입 정보 없이도 잡을 수 있는 결함이다. AST로 충분하다.

## 한계

이 검사는 `self.<name>`을 콜백 자리에 그대로 넘긴 경우만 본다. 다음은 보지 않는다.

* 부모 클래스나 믹스인에서 물려받은 콜백 — 타입 추적이 필요하다
* `functools.partial`, 람다, 지역 함수로 감싼 콜백
* 문자열로 만들어 `getattr`로 꺼내는 콜백

이 저장소의 노드는 모두 `Node`를 직접 상속하고 콜백을 같은 클래스에 둔다. 그
범위에서 이 검사는 이번 결함을 확실히 잡는다. 범위를 넘는 패턴이 생기면 그때
넓힌다 — 지금 없는 것을 미리 다루면 검사가 복잡해져 아무도 고치지 않게 된다.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SEARCH_ROOT = REPO_ROOT / 'jetson' / 'ros2_ws' / 'src'

# 콜백을 받는 호출과 그 인자 위치(0부터).
#
# rclpy 시그니처 기준이다. 위치 인자로 넘긴 경우와 키워드로 넘긴 경우를 모두 본다.
CALLBACK_SLOTS: dict[str, tuple[int, str]] = {
    'create_subscription': (2, 'callback'),
    'create_timer': (1, 'callback'),
    'create_service': (2, 'callback'),
    'create_guard_condition': (0, 'callback'),
    'add_on_set_parameters_callback': (0, 'callback'),
}


def _callback_name(call: ast.Call, index: int, keyword: str) -> str | None:
    """호출에서 `self.<name>` 형태의 콜백 이름을 꺼낸다. 아니면 None."""
    node: ast.expr | None = None
    if len(call.args) > index:
        node = call.args[index]
    else:
        for kw in call.keywords:
            if kw.arg == keyword:
                node = kw.value
                break
    if node is None:
        return None
    # self.<name> 만 본다. 그 밖의 형태는 위 docstring의 「한계」 참고.
    if (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == 'self'
    ):
        return node.attr
    return None


def _defined_names(class_node: ast.ClassDef) -> set[str]:
    """클래스에 정의된 메서드와 `self.x = ...` 로 대입된 속성 이름."""
    names: set[str] = set()
    for node in ast.walk(class_node):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if (
                    isinstance(target, ast.Attribute)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == 'self'
                ):
                    names.add(target.attr)
        elif isinstance(node, ast.AnnAssign):
            target = node.target
            if (
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id == 'self'
            ):
                names.add(target.attr)
    return names


def check_source(source: str, label: str) -> list[str]:
    """소스 하나를 검사해 문제 목록을 돌려준다. 비어 있으면 통과다."""
    try:
        tree = ast.parse(source)
    except SyntaxError as error:
        return [f'{label}: 파싱 실패 — {error}']

    problems: list[str] = []
    for class_node in (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)):
        defined = _defined_names(class_node)
        for call in (n for n in ast.walk(class_node) if isinstance(n, ast.Call)):
            func = call.func
            if not isinstance(func, ast.Attribute):
                continue
            slot = CALLBACK_SLOTS.get(func.attr)
            if slot is None:
                continue
            name = _callback_name(call, *slot)
            if name is None or name in defined:
                continue
            problems.append(
                f'{label}:{call.lineno}: {class_node.name}.{name} 이(가) '
                f'{func.attr} 콜백으로 넘겨졌지만 클래스에 정의가 없다'
            )
    return problems


def main() -> int:
    if not SEARCH_ROOT.is_dir():
        print(f'검사 대상 디렉터리가 없다: {SEARCH_ROOT}', file=sys.stderr)
        return 1

    problems: list[str] = []
    checked = 0
    for path in sorted(SEARCH_ROOT.rglob('*.py')):
        # 서드파티 드라이버와 빌드 산출물은 우리 책임이 아니다.
        parts = set(path.parts)
        if parts & {'build', 'install', '__pycache__', 'ydlidar_ros2_driver', 'usb_cam'}:
            continue
        checked += 1
        problems += check_source(path.read_text(encoding='utf-8'),
                                 str(path.relative_to(REPO_ROOT)))

    if problems:
        print(f'노드 콜백 검사 실패 ({len(problems)}건):', file=sys.stderr)
        for problem in problems:
            print(f'  {problem}', file=sys.stderr)
        print('', file=sys.stderr)
        print('구독·타이머·서비스에 넘긴 콜백이 그 클래스에 없다. 기동 즉시 '
              'AttributeError 로 죽는다.', file=sys.stderr)
        return 1

    print(f'노드 콜백 검사 통과 (파일 {checked}개).')
    return 0


if __name__ == '__main__':
    sys.exit(main())
