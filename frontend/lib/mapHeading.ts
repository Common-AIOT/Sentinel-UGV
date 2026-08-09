/**
 * ROS map yaw를 관제 지도 화살표에 적용하는 규칙.
 *
 * 센서·EKF 값은 REP-103을 그대로 유지한다(반시계 양수, 시계 음수). 그쪽 부호를
 * 바꾸면 안 된다 — 관제뿐 아니라 TF와 Nav2까지 반대 방향을 쓰게 된다.
 *
 * ## 화면 회전은 map yaw의 반대 부호다 (S15P11A301-364)
 *
 * 지도 캔버스가 **상하 반전**되어 있기 때문이다. ROS OccupancyGrid는 row가
 * 커질수록 map +y이고(수학 좌표계), 캔버스는 y가 아래로 증가한다. 그래서
 * `LiveMap.renderGrid`가 `height-1-row`로 뒤집어 그리고 `worldToPixel`도 같은
 * 규칙으로 로봇 위치를 뒤집는다. 그 좌표계에서 회전만 안 뒤집으면 화살표가
 * 실제와 상하 대칭인 방향을 가리킨다.
 *
 *     map 정면 벡터 (cos yaw,  sin yaw)      ← +y가 화면 위쪽
 *     화면 정면 벡터 (cos yaw, -sin yaw)      ← y가 아래로 증가
 *     따라서 화면각 = -yaw
 *
 * **종전 주석은 「-yaw를 넣었더니 반대로 보여서 yaw로 되돌렸다」고 적고 있었다.**
 * 그 관찰을 지우지 않고 남긴다 — 다만 그때는 위 반전 규칙이 함께 성립하지 않는
 * 상태였을 것이다. 2026-08-09 실기동에서 이 함수가 yaw를 그대로 반환한 결과가
 * 실측으로 확인됐다: `/map` 격자에서 로봇 정면 3m는 자유·후방 3m는 점유인데
 * 화면에서는 반대로 보여, 로봇이 벽으로 가는 것처럼 읽혔다.
 *
 * 이 규칙은 아래 파일의 시험이 **화면 벡터로** 고정한다. 부호만 비교하는 시험은
 * 왕복을 막지 못하므로 쓰지 않는다.
 */
export function arrowRotationFromMapYaw(yaw: number): number {
  return -yaw;
}

/**
 * 화살표가 화면에서 실제로 가리키는 단위 벡터. 시험과 디버깅용이며,
 * 캔버스 규약(x 오른쪽, y 아래)을 그대로 쓴다.
 */
export function arrowScreenVector(yaw: number): { x: number; y: number } {
  const angle = arrowRotationFromMapYaw(yaw);
  return { x: Math.cos(angle), y: Math.sin(angle) };
}

/** 관제에 표시할 map 기준 각도. 입력 yaw는 quaternion 디코더가 [-π, π]로 만든다. */
export function mapYawDegrees(yaw: number): number {
  return (yaw * 180) / Math.PI;
}
