import type { TelemetryLatest } from "@/lib/api";
import type { MotionReading } from "@/features/robot/mockData";

/**
 * 최신값 응답 → 주행 지표 (S15P11A301-300).
 *
 * 순수 함수로 뺀 이유는 실제 버그 때문이다. 이 필드들은 #300 에서 응답에 더한 것이라
 * **백엔드 배포 전에는 응답에 키 자체가 없어 `undefined` 가 온다.** 타입은
 * `number | null` 이지만 그 약속을 런타임이 지키는 것은 배포 이후이고, 화면이
 * `value === null` 로만 검사하면 `undefined.toFixed()` 로 페이지가 죽는다.
 *
 * 같은 위험은 앞으로 응답에 필드를 더할 때마다 생긴다. 그래서 판정을 화면 밖으로
 * 꺼내 시험이 지키게 한다 — environmentThresholds 와 같은 이유다.
 *
 * 신선도는 `poseTime` 으로 판정한다. 온습도(environment_metrics)·MCU(robot_metrics)와
 * 다른 하이퍼테이블(robot_pose)에서 오므로 다른 시각을 쓰면 판정이 틀어진다.
 */
export const MOTION_FRESH_MS = 60_000;

export function motionFromLatest(d: TelemetryLatest, now: number): MotionReading {
  const poseTime = d.poseTime == null ? null : Date.parse(d.poseTime);
  const fresh = poseTime !== null && now - poseTime <= MOTION_FRESH_MS;
  const pick = (v: number | null | undefined) => (fresh ? v ?? null : null);
  return {
    linearVelocity: pick(d.linearVelocity),
    angularVelocity: pick(d.angularVelocity),
    updatedAt: fresh ? poseTime : null,
  };
}
