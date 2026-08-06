#!/usr/bin/env python3
"""탐사 목표를 주행으로 바꾸는 층 (S15P11A301-172).

`ExplorationNode` 는 「어디로 갈지」만 결정하고, 「어떻게 가는지」는 이 인터페이스
뒤에 있다. 덕분에 Nav2 없이도 선택 로직을 실기동으로 확인할 수 있고, 데모를 순찰
시퀀스로 축소할 때도 노드를 건드리지 않는다.

## 결과를 콜백이 아니라 폴링으로 넘기는 이유

노드의 판정은 2초 주기 `_select()` 한 곳에서만 일어난다. 도달·실패를 콜백에서
직접 처리하면 약속(commitment) 해제와 후보 재선택이 두 군데로 갈라지고, 단일
스레드 실행기에서 타이머와 콜백이 섞이는 순서를 따라가야 한다. 결과를 담아 두고
`take_outcome()` 으로 소비하면 상태 변화가 한 곳에 남는다.

## 왜 「서버 없음」이 실패와 다른가

Nav2 는 lifecycle 노드 묶음이라 기동 후 활성까지 몇 초 걸린다. 그동안의 목표를
실패로 세면 **블랙리스트가 정상 후보를 3회 만에 먹어치운다.** 기동 직후에 그러면
탐사가 시작도 못 하고 끝난다. 그래서 `UNAVAILABLE` 을 따로 둔다 — 약속만 풀고
실패는 적립하지 않는다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from geometry_msgs.msg import PoseStamped
from rclpy.action import ActionClient
from rclpy.node import Node

STATUS_SUCCEEDED = 'SUCCEEDED'
STATUS_FAILED = 'FAILED'
STATUS_CANCELED = 'CANCELED'
STATUS_UNAVAILABLE = 'UNAVAILABLE'


@dataclass(frozen=True)
class GoalOutcome:
    """끝난 목표 하나의 결말. 좌표를 함께 담아 블랙리스트가 자리를 알 수 있게 한다."""

    status: str
    x: float
    y: float


def pose_stamped(x: float, y: float, yaw: float, stamp) -> PoseStamped:
    """map 프레임 목표 자세. 두 navigator 가 같은 변환을 쓴다."""
    pose = PoseStamped()
    pose.header.frame_id = 'map'
    pose.header.stamp = stamp
    pose.pose.position.x = x
    pose.pose.position.y = y
    pose.pose.orientation.z = math.sin(yaw / 2.0)
    pose.pose.orientation.w = math.cos(yaw / 2.0)
    return pose


class NullNavigator:
    """목표를 받기만 하고 이동은 없다. Nav2 를 끈 구성의 기본값이다.

    도달을 보고하지 않으므로 노드는 약속을 유지한다 — 그것이 정직한 표현이다.
    로그에 목표가 계속 찍히면 "선택은 되는데 주행이 없다"가 보인다.
    """

    def __init__(self, node: Node) -> None:
        self._node = node

    def send_goal(self, x: float, y: float, yaw: float) -> None:
        self._node.get_logger().info(
            f'목표 선택 (주행 없음 — navigator=null): '
            f'({x:.2f}, {y:.2f}, {math.degrees(yaw):.0f}°)'
        )

    def cancel(self) -> None:
        pass

    def take_outcome(self) -> GoalOutcome | None:
        return None


class Nav2Navigator:
    """`NavigateToPose` 액션으로 목표를 보낸다.

    새 목표를 보낼 때 이전 것을 명시적으로 취소하지 않는다 — BT navigator 가
    선점(preempt)을 처리하며, 취소를 먼저 보내면 그 왕복 동안 로봇이 멈춘다.
    약속 정책이 목표 교체 자체를 드물게 만들어 두었으므로 선점에 맡긴다.
    """

    def __init__(self, node: Node, action_name: str = 'navigate_to_pose') -> None:
        self._node = node
        self._client = ActionClient(node, self._action_type(), action_name)
        self._action_name = action_name
        self._goal_handle = None
        self._pending: tuple[float, float] | None = None
        self._outcome: GoalOutcome | None = None
        self._warned_unavailable = False

    @staticmethod
    def _action_type():
        # import 를 여기 두는 이유: nav2_msgs 가 없는 개발 기기에서도 이 모듈을
        # import 할 수 있어야 한다(순수 로직 시험이 같은 패키지에 있다).
        from nav2_msgs.action import NavigateToPose

        return NavigateToPose

    # ── 명령 ────────────────────────────────────────────────────────────────

    def send_goal(self, x: float, y: float, yaw: float) -> None:
        if not self._client.server_is_ready():
            # 기동 직후의 정상 상황이다. 실패로 세지 않는다 — 위 주석 참고.
            self._outcome = GoalOutcome(STATUS_UNAVAILABLE, x, y)
            if not self._warned_unavailable:
                self._warned_unavailable = True
                self._node.get_logger().warn(
                    f'{self._action_name} 액션 서버가 아직 없다. Nav2 가 활성인지 '
                    '확인한다: ros2 lifecycle get /bt_navigator'
                )
            return
        self._warned_unavailable = False

        goal = self._action_type().Goal()
        goal.pose = pose_stamped(x, y, yaw, self._node.get_clock().now().to_msg())
        self._pending = (x, y)
        self._node.get_logger().info(
            f'목표 전송: ({x:.2f}, {y:.2f}, {math.degrees(yaw):.0f}°)'
        )
        self._client.send_goal_async(goal).add_done_callback(self._on_accepted)

    def cancel(self) -> None:
        if self._goal_handle is None:
            return
        self._goal_handle.cancel_goal_async()
        self._goal_handle = None
        self._pending = None

    def take_outcome(self) -> GoalOutcome | None:
        """결말을 한 번만 돌려준다. 소비하면 비워진다."""
        outcome, self._outcome = self._outcome, None
        return outcome

    # ── 콜백 ────────────────────────────────────────────────────────────────

    def _on_accepted(self, future) -> None:
        handle = future.result()
        target = self._pending
        if target is None:
            return
        if not handle.accepted:
            # 계획 자체가 불가능한 목표다. 미지 공간 안이거나 벽 안쪽이다.
            self._node.get_logger().warn(
                f'목표 거부: ({target[0]:.2f}, {target[1]:.2f}). '
                'allow_unknown=false 이므로 미지 공간 목표는 계획되지 않는다'
            )
            self._outcome = GoalOutcome(STATUS_FAILED, target[0], target[1])
            self._pending = None
            return
        self._goal_handle = handle
        handle.get_result_async().add_done_callback(self._on_result)

    def _on_result(self, future) -> None:
        target = self._pending
        self._pending = None
        self._goal_handle = None
        if target is None:
            return
        # STATUS_SUCCEEDED = 4 (action_msgs/GoalStatus). 문자열 비교를 피하려고
        # 숫자를 쓰지만, 의미가 보이게 이름을 남긴다.
        from action_msgs.msg import GoalStatus

        status = future.result().status
        if status == GoalStatus.STATUS_SUCCEEDED:
            self._node.get_logger().info(
                f'목표 도달: ({target[0]:.2f}, {target[1]:.2f})'
            )
            self._outcome = GoalOutcome(STATUS_SUCCEEDED, target[0], target[1])
        elif status == GoalStatus.STATUS_CANCELED:
            self._outcome = GoalOutcome(STATUS_CANCELED, target[0], target[1])
        else:
            self._node.get_logger().warn(
                f'목표 실패(status={status}): ({target[0]:.2f}, {target[1]:.2f})'
            )
            self._outcome = GoalOutcome(STATUS_FAILED, target[0], target[1])
