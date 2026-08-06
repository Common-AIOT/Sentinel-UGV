"""관제용 map 기준 고주기 자세를 발행한다.

``slam_toolbox``의 ``/pose``는 scan 처리 주기(약 4~5Hz)에 맞춰 나온다. 반면
``robot_localization`` EKF가 IMU yaw rate를 적분해 내는 ``odom→base_footprint``는
30Hz다. 관제 화살표가 ``/pose``만 읽으면 EKF가 이미 새 방향을 알고 있어도 다음
scan까지 화면이 움직이지 않는다.

이 노드는 TF의 ``map→base_footprint``를 주기적으로 조회해 ``/pose/fused``로
발행한다. 이 변환에는 두 소스가 이미 올바른 프레임으로 합성돼 있다.

* ``map→odom``: slam_toolbox의 장기 보정
* ``odom→base_footprint``: EKF의 엔코더 vx + IMU vyaw

``/odometry/filtered``의 odom yaw를 ``/pose``의 map 위치와 직접 섞지 않는 것이
중요하다. 그렇게 하면 ``map→odom``의 회전 보정이 빠져 지도와 화살표 방향이
어긋난다.

SLAM이 아직 첫 pose를 내지 않았거나 ``/map`` 발행자가 사라지면 발행을 멈춘다.
마지막 TF를 계속 내보내면 SLAM이 죽었는데도 관제에서는 로봇이 살아 움직이는 것처럼
보이기 때문이다. 프론트엔드는 이 토픽이 끊기면 기존 ``/pose``로 되돌아간다.
"""

from __future__ import annotations

import rclpy
from geometry_msgs.msg import PoseWithCovarianceStamped
from rclpy.node import Node
from rclpy.time import Time
import tf2_ros

from .transform_composition import compose_transforms


class FusedPosePublisher(Node):
    """최신 TF를 관제가 이미 읽는 pose 메시지 형식으로 변환한다."""

    def __init__(self) -> None:
        super().__init__('fused_pose_publisher')

        self.declare_parameter('map_frame', 'map')
        self.declare_parameter('odom_frame', 'odom')
        self.declare_parameter('base_frame', 'base_footprint')
        self.declare_parameter('map_topic', '/map')
        self.declare_parameter('slam_pose_topic', '/pose')
        self.declare_parameter('output_topic', '/pose/fused')
        self.declare_parameter('publish_rate_hz', 20.0)

        self._map_frame = str(self.get_parameter('map_frame').value)
        self._odom_frame = str(self.get_parameter('odom_frame').value)
        self._base_frame = str(self.get_parameter('base_frame').value)
        self._map_topic = str(self.get_parameter('map_topic').value)
        self._have_slam_pose = False
        self._covariance = [0.0] * 36
        self._tf_available = False

        self._publisher = self.create_publisher(
            PoseWithCovarianceStamped,
            str(self.get_parameter('output_topic').value),
            10,
        )
        self.create_subscription(
            PoseWithCovarianceStamped,
            str(self.get_parameter('slam_pose_topic').value),
            self._on_slam_pose,
            10,
        )

        self._tf_buffer = tf2_ros.Buffer()
        self._tf_listener = tf2_ros.TransformListener(self._tf_buffer, self)

        publish_rate_hz = float(self.get_parameter('publish_rate_hz').value)
        if publish_rate_hz <= 0.0:
            raise ValueError('publish_rate_hz must be greater than zero')
        self.create_timer(1.0 / publish_rate_hz, self._publish)

    def _on_slam_pose(self, message: PoseWithCovarianceStamped) -> None:
        """SLAM 초기화 여부와 가장 최근 위치 공분산을 보관한다."""
        if message.header.frame_id != self._map_frame:
            return
        self._covariance = list(message.pose.covariance)
        self._have_slam_pose = True

    def _publish(self) -> None:
        # 첫 scan match 전에는 map 기준 pose가 정의되지 않는다.
        if not self._have_slam_pose:
            return

        # TF buffer는 발행자가 죽은 뒤에도 마지막 값을 잠시 보관한다. /map 발행자가
        # 없으면 그 오래된 값을 새 자세처럼 재발행하지 않는다.
        if self.count_publishers(self._map_topic) == 0:
            self._tf_available = False
            return

        try:
            # 체인 전체를 latest로 조회하면 최신 공통 시각이 느린 map→odom 갱신
            # 시각으로 제한된다. 두 변환을 따로 조회해야 30Hz EKF 회전이 다음
            # SLAM scan match를 기다리지 않고 관제에 반영된다.
            map_to_odom = self._tf_buffer.lookup_transform(
                self._map_frame,
                self._odom_frame,
                Time(),
            )
            odom_to_base = self._tf_buffer.lookup_transform(
                self._odom_frame,
                self._base_frame,
                Time(),
            )
        except (
            tf2_ros.LookupException,
            tf2_ros.ConnectivityException,
            tf2_ros.ExtrapolationException,
        ):
            self._tf_available = False
            return

        if not self._tf_available:
            self.get_logger().info(
                f'{self._map_frame}->{self._base_frame} 융합 자세 발행 시작'
            )
            self._tf_available = True

        map_translation = map_to_odom.transform.translation
        map_rotation = map_to_odom.transform.rotation
        odom_translation = odom_to_base.transform.translation
        odom_rotation = odom_to_base.transform.rotation
        translation, rotation = compose_transforms(
            (map_translation.x, map_translation.y, map_translation.z),
            (map_rotation.x, map_rotation.y, map_rotation.z, map_rotation.w),
            (odom_translation.x, odom_translation.y, odom_translation.z),
            (odom_rotation.x, odom_rotation.y, odom_rotation.z, odom_rotation.w),
        )

        message = PoseWithCovarianceStamped()
        # latest TF를 지금 시점의 화면용 상태로 내보낸다. transform의 stamp는 합성
        # 체인에서 더 느린 map→odom 갱신 시각일 수 있어 그대로 쓰면 20Hz 발행인데도
        # 관제가 오래된 메시지로 오인한다.
        message.header.stamp = self.get_clock().now().to_msg()
        message.header.frame_id = self._map_frame
        message.pose.pose.position.x = translation[0]
        message.pose.pose.position.y = translation[1]
        message.pose.pose.position.z = translation[2]
        message.pose.pose.orientation.x = rotation[0]
        message.pose.pose.orientation.y = rotation[1]
        message.pose.pose.orientation.z = rotation[2]
        message.pose.pose.orientation.w = rotation[3]
        # TF 자체에는 covariance가 없으므로 가장 최근 SLAM pose의 값을 보존한다.
        message.pose.covariance = list(self._covariance)
        self._publisher.publish(message)


def main(args=None) -> None:
    rclpy.init(args=args)
    node = FusedPosePublisher()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
