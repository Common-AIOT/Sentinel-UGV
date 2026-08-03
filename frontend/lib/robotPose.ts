/**
 * `geometry_msgs/PoseWithCovarianceStamped` CDR 디코더 (S15P11A301-227).
 *
 * `slam_toolbox`가 `/pose`로 추정 위치를 4~5Hz로 낸다. 미니맵에 로봇이 어디 있고
 * 어디를 보는지 찍는 데 쓴다.
 *
 * ## 레이아웃은 실측으로 확정했다
 *
 * 실제 메시지 364바이트를 캡처해 바이트가 정확히 소진되는 것을 확인했다.
 *
 * ```text
 * 0    encapsulation 00 01 00 00
 * --- 이후 오프셋은 본문(4) 기준 ---
 * 0    stamp.sec int32,  stamp.nanosec uint32
 * 8    frame_id  uint32 길이 + 바이트("map\0")
 * 16   position     3 × float64      ← 이미 8정렬이라 패딩이 없다
 * 40   orientation  4 × float64
 * 72   covariance   36 × float64     고정 배열이라 길이 접두가 없다
 * 360  끝  (+4 = 364)
 * ```
 *
 * **`OccupancyGrid`와 패딩이 다르다.** 그쪽은 `width`·`height`(본문 36) 뒤에
 * 4바이트 패딩이 들어가는데 여기는 같은 자리에 없다. 그래서 정렬을 손으로 세지
 * 않고 `CdrReader`에 맡긴다 — 두 메시지를 같은 규칙으로 읽으려다 한쪽을 틀리는
 * 것이 이 종류의 흔한 실수다.
 *
 * `covariance`는 **길이 접두가 없다.** `double[36]` 고정 배열이기 때문이다.
 * 시퀀스로 읽으면 첫 8바이트를 길이로 해석해 그 뒤가 전부 밀린다.
 */

import { CdrError, CdrReader } from "./cdr";

export interface RobotPose {
  frameId: string;
  x: number;
  y: number;
  /** 라디안. REP-103 대로 반시계 방향이 양수다. */
  yaw: number;
  /** x·y 표준편차(m)와 yaw 표준편차(rad). 화면에 쓰지 않아도 판단 근거가 된다. */
  stdDevX: number;
  stdDevY: number;
  stdDevYaw: number;
}

/** covariance 대각 성분의 인덱스. 6×6 행렬을 평탄화한 것이다. */
const COV_XX = 0;
const COV_YY = 7;
const COV_YAW = 35;
const COV_LENGTH = 36;

/**
 * 쿼터니언에서 yaw만 뽑는다.
 *
 * 2D SLAM이므로 roll·pitch는 쓰지 않는다. 젯슨 쪽 `message_mapper.py`가 같은
 * 공식을 쓰고 있어 두 곳의 값이 일치한다.
 */
function yawFromQuaternion(x: number, y: number, z: number, w: number): number {
  return Math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z));
}

export function decodeRobotPose(buffer: ArrayBuffer): RobotPose {
  const reader = new CdrReader(buffer);

  reader.int32(); // stamp.sec
  reader.uint32(); // stamp.nanosec
  const frameId = reader.string();

  const x = reader.float64();
  const y = reader.float64();
  reader.float64(); // position.z — 2D 다

  const qx = reader.float64();
  const qy = reader.float64();
  const qz = reader.float64();
  const qw = reader.float64();

  // 고정 배열이므로 길이 접두가 없다. 하나씩 읽는다.
  const covariance = new Array<number>(COV_LENGTH);
  for (let i = 0; i < COV_LENGTH; i += 1) covariance[i] = reader.float64();

  if (reader.consumed !== buffer.byteLength) {
    // 남거나 모자라면 레이아웃 이해가 틀린 것이다. covariance 를 시퀀스로 읽는
    // 실수가 정확히 여기서 걸린다.
    throw new CdrError(
      `바이트가 남았습니다: ${reader.consumed} 소비 / ${buffer.byteLength} 전체`,
    );
  }

  return {
    frameId,
    x,
    y,
    yaw: yawFromQuaternion(qx, qy, qz, qw),
    // 분산이므로 제곱근이 표준편차다. slam_toolbox 는 이 값을 실제로 계산해
    // 내보낸다(표본마다 변하는 것을 실측으로 확인했다).
    stdDevX: Math.sqrt(Math.abs(covariance[COV_XX])),
    stdDevY: Math.sqrt(Math.abs(covariance[COV_YY])),
    stdDevYaw: Math.sqrt(Math.abs(covariance[COV_YAW])),
  };
}
