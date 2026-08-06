/**
 * ROS map yaw를 관제 지도 화살표에 적용하는 규칙.
 *
 * 센서·EKF 값은 REP-103을 그대로 유지한다.
 *
 * - 반시계 방향: 양수
 * - 시계 방향: 음수
 *
 * 실차 관제 화면에서 Canvas에 `-yaw`를 전달했을 때 회전 방향이 반대로 표시되는
 * 것을 확인했으므로, 화살표 회전에는 yaw를 같은 부호로 전달한다. 센서나 EKF에서
 * 부호를 바꾸지 않는 것이 중요하다. 그쪽을 뒤집으면 관제뿐 아니라 TF와 Nav2까지
 * 반대 방향을 사용하게 된다.
 */
export function arrowRotationFromMapYaw(yaw: number): number {
  return yaw;
}

/** 관제에 표시할 map 기준 각도. 입력 yaw는 quaternion 디코더가 [-π, π]로 만든다. */
export function mapYawDegrees(yaw: number): number {
  return (yaw * 180) / Math.PI;
}
